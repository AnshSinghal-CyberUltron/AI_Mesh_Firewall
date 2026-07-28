"""RED-TEAM L5-05: the Django admin must not render upstream secrets, nor cross tenants.

``MCPServerRegistration``'s credential columns are ``EncryptedCharField``s that DECRYPT on
read. ``MCPServerRegistrationAdmin`` previously declared only list_display/list_filter/
search_fields, so ``ModelAdmin.get_form()`` built a ModelForm over ALL editable fields and
rendered the PLAINTEXT into the change-form widget — and with no org-scoped queryset, any
``is_staff`` user could read EVERY tenant's bearer tokens, basic-auth passwords, custom auth
headers, OAuth client secrets, refresh tokens and PKCE verifiers. That defeats the REST
layer's ``write_only`` protection and tenant isolation at once.

Run: cd control && DEBUG=True DATABASE_URL=postgresql://... \
  .venv/bin/python manage.py test mcp_connector.tests.test_admin_secret_exposure --noinput
"""
from __future__ import annotations

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from auth.models import Organization, UserProfile
from mcp_connector.admin import _SECRET_FIELDS, MCPServerRegistrationAdmin
from mcp_connector.models import MCPServerRegistration

User = get_user_model()

SECRET = "SECRET-BEARER-DO-NOT-RENDER"


class AdminSecretExposureTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organization.objects.create(name="Org A", slug="org-a")
        cls.org_b = Organization.objects.create(name="Org B", slug="org-b")
        cls.srv_a = MCPServerRegistration.objects.create(
            organization=cls.org_a, name="srv-a", url="https://a.example/mcp",
            transport="streamable_http", auth_type="bearer", auth_token=SECRET,
            oauth_client_secret=SECRET, oauth_refresh_token=SECRET,
        )
        cls.srv_b = MCPServerRegistration.objects.create(
            organization=cls.org_b, name="srv-b", url="https://b.example/mcp",
            transport="streamable_http", auth_type="bearer", auth_token=SECRET,
        )

    def setUp(self):
        self.admin = MCPServerRegistrationAdmin(MCPServerRegistration, AdminSite())
        self.rf = RequestFactory()

    def _req(self, user):
        req = self.rf.get("/admin/mcp_connector/mcpserverregistration/")
        req.user = user
        return req

    # ── the secrets must not be form fields at all ──────────────────────────────────
    def test_secret_fields_are_not_in_the_admin_form(self):
        staff = User.objects.create_user("staff1", password="x", is_staff=True)
        form = self.admin.get_form(self._req(staff))
        for name in _SECRET_FIELDS:
            self.assertNotIn(
                name, form.base_fields,
                f"{name} is a form field — the admin would render the DECRYPTED value",
            )

    def test_rendered_form_html_contains_no_plaintext_secret(self):
        """Belt-and-braces: the value must not reach the HTML by any route."""
        staff = User.objects.create_user("staff2", password="x", is_staff=True)
        form = self.admin.get_form(self._req(staff))(instance=self.srv_a)
        self.assertNotIn(SECRET, form.as_p(), "plaintext secret rendered in the admin form")

    def test_decryption_still_works_so_the_test_is_meaningful(self):
        """Guard against a vacuous test: the column really does decrypt on read."""
        self.assertEqual(
            MCPServerRegistration.objects.get(pk=self.srv_a.pk).auth_token, SECRET
        )

    # ── tenant isolation on the changelist ──────────────────────────────────────────
    def test_staff_user_sees_only_their_own_org(self):
        staff = User.objects.create_user("staff3", password="x", is_staff=True)
        UserProfile.objects.create(user=staff, organization=self.org_a)
        names = set(self.admin.get_queryset(self._req(staff)).values_list("name", flat=True))
        self.assertEqual(names, {"srv-a"}, "cross-tenant server visible in admin")

    def test_staff_user_without_org_sees_nothing(self):
        """Fail closed rather than exposing every tenant."""
        staff = User.objects.create_user("staff4", password="x", is_staff=True)
        UserProfile.objects.create(user=staff, organization=None)
        self.assertEqual(self.admin.get_queryset(self._req(staff)).count(), 0)

    def test_superuser_keeps_the_cross_tenant_view(self):
        su = User.objects.create_superuser("root", "root@example.com", "x")
        self.assertEqual(self.admin.get_queryset(self._req(su)).count(), 2)

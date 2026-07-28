"""L5-05 (lifecycle red-team wf_c7ea99b8): MCPServerRegistrationAdmin must never render
the EncryptedCharField upstream credentials — they decrypt on read, so a change form
without `exclude` leaked auth_token / auth_password / oauth_client_secret / refresh_token /
code_verifier in cleartext for every tenant to any is_staff user.
"""
from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase

from policy.encrypted_fields import EncryptedCharField
from mcp_connector.admin import MCPServerRegistrationAdmin
from mcp_connector.models import MCPServerRegistration


class AdminSecretExposureTests(SimpleTestCase):
    def _encrypted_field_names(self):
        return {
            f.name for f in MCPServerRegistration._meta.get_fields()
            if isinstance(f, EncryptedCharField)
        }

    def test_all_encrypted_fields_excluded(self):
        encrypted = self._encrypted_field_names()
        self.assertTrue(encrypted, "sanity: the model has encrypted secret fields")
        excluded = set(MCPServerRegistrationAdmin.exclude or ())
        leaked = encrypted - excluded
        self.assertEqual(leaked, set(), f"admin exposes decrypted secrets: {leaked}")

    def test_admin_form_has_no_secret_fields(self):
        # The actual generated ModelForm must not carry any encrypted field.
        admin_obj = MCPServerRegistrationAdmin(MCPServerRegistration, AdminSite())
        form_fields = set(admin_obj.get_form(request=None).base_fields.keys())
        leaked = self._encrypted_field_names() & form_fields
        self.assertEqual(leaked, set(), f"admin form renders secrets: {leaked}")

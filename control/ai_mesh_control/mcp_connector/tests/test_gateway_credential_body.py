"""GW17b: the credential wire shape (pure; no database)."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from mcp_connector import views


def _server(**over):
    base = dict(
        auth_type="oauth", credential_version=3, auth_token="access-token-1",
        oauth_token_expires_at=None, oauth_refresh_token="refresh-1",
        oauth_token_endpoint="https://idp.example.com/t", oauth_client_id="cid",
        oauth_client_secret="csecret", auth_username="u", auth_password="pw-secret-1",
        auth_header_key="X-Api-Key", auth_header_value="hv-secret-1",
    )
    base.update(over)
    return SimpleNamespace(**base)


class CredentialBodyTests(SimpleTestCase):
    def test_refresh_material_only_on_request(self):
        plain = views._credential_body(_server(), need_refresh=False)
        asked = views._credential_body(_server(), need_refresh=True)
        self.assertNotIn("refresh_token", plain)
        self.assertEqual(asked["refresh_token"], "refresh-1")
        self.assertEqual(asked["token_endpoint"], "https://idp.example.com/t")

    def test_oauth_without_refresh_token_is_not_refreshable(self):
        body = views._credential_body(_server(oauth_refresh_token=""), need_refresh=True)
        self.assertFalse(body["refreshable"])
        self.assertNotIn("refresh_token", body)

    def test_authheaders_is_header_and_basic_uses_password(self):
        header = views._credential_body(_server(auth_type="authheaders"), need_refresh=False)
        self.assertEqual((header["auth_type"], header["header_name"]), ("header", "X-Api-Key"))
        self.assertEqual(header["token"], "hv-secret-1")
        basic = views._credential_body(_server(auth_type="basic"), need_refresh=False)
        self.assertEqual((basic["username"], basic["token"]), ("u", "pw-secret-1"))

    def test_types_without_a_credential_return_none(self):
        for kind in ("none", "query_param", "unknown"):
            self.assertIsNone(views._credential_body(_server(auth_type=kind), need_refresh=False))

    def test_version_is_carried(self):
        self.assertEqual(views._credential_body(_server(), need_refresh=False)["version"], 3)

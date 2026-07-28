"""Pure helper tests for Module 2 kill-switch containment projection."""

from django.test import SimpleTestCase


class GatewayEnforcedKillModelTests(SimpleTestCase):
    def test_real_model_is_enforced(self):
        from module2.views import _is_gateway_enforced_kill_model

        self.assertTrue(_is_gateway_enforced_kill_model("gpt-4o"))
        self.assertTrue(_is_gateway_enforced_kill_model("north-mini"))

    def test_legacy_credential_scope_not_enforced(self):
        from module2.views import _is_gateway_enforced_kill_model

        self.assertFalse(_is_gateway_enforced_kill_model("__credential__"))
        self.assertFalse(_is_gateway_enforced_kill_model(""))
        self.assertFalse(_is_gateway_enforced_kill_model(None))

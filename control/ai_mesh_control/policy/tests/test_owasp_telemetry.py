"""OWASP telemetry resolution and dashboard coverage semantics."""

from django.test import SimpleTestCase

from ai_mesh_shared.owasp_telemetry import (
    is_owasp_enforced,
    resolve_owasp_codes,
    THREAT_TYPE_TO_OWASP,
)


class OwaspTelemetryResolverTests(SimpleTestCase):
    def test_threat_type_maps_to_llm_code(self):
        codes = resolve_owasp_codes("prompt_injection", {})
        self.assertEqual(codes, ["LLM01"])

    def test_bedrock_rule_id_pass_through(self):
        codes = resolve_owasp_codes(
            "data_leakage",
            {
                "raw_findings": [
                    {"rule_id": "LLM06", "category": "data_leakage"},
                    {"rule_id": "LLM01", "category": "prompt_injection"},
                ],
            },
        )
        self.assertIn("LLM06", codes)
        self.assertIn("LLM01", codes)

    def test_mcp_threat_type(self):
        self.assertEqual(THREAT_TYPE_TO_OWASP.get("tool_overreach"), "MCP01")
        codes = resolve_owasp_codes("mcp_injection", {})
        self.assertEqual(codes, ["MCP04"])

    def test_explicit_owasp_codes_list(self):
        codes = resolve_owasp_codes("", {"owasp_codes": ["LLM09", "LLM09"]})
        self.assertEqual(codes, ["LLM09"])

    def test_enforced_actions_include_redact_and_rewrite(self):
        self.assertTrue(is_owasp_enforced("block"))
        self.assertTrue(is_owasp_enforced("redact"))
        self.assertTrue(is_owasp_enforced("rewrite"))
        self.assertFalse(is_owasp_enforced("monitor"))
        self.assertFalse(is_owasp_enforced("allow"))

import unittest

from ai_mesh_gateway.main import _build_zeroshield_metadata
from ai_mesh_gateway.patterns import redact_all


class OutputGuardrailMetadataTests(unittest.TestCase):
    def test_build_zeroshield_metadata_can_carry_output_redaction_details(self):
        metadata = _build_zeroshield_metadata(
            action="redact",
            reason="Output redacted before delivery.",
            detection_tier="output_guard",
            threat_type="pii",
            confidence=0.91,
            matched_patterns=["ssn"],
            original_prompt="user prompt",
            redacted_response="Safe response with [REDACTED]",
            detail="Detected SSN in generated response.",
            processing_time_ms=12.5,
        )

        self.assertEqual(metadata["action"], "redact")
        self.assertEqual(metadata["redacted_response"], "Safe response with [REDACTED]")
        self.assertEqual(metadata["detection_tier"], "output_guard")

    def test_build_zeroshield_metadata_can_carry_output_rewrite_details(self):
        metadata = _build_zeroshield_metadata(
            action="rewrite",
            reason="Unsafe output was rewritten before delivery.",
            detection_tier="output_guard",
            threat_type="hallucination",
            confidence=0.72,
            matched_patterns=["fabricated_citation"],
            original_prompt="user prompt",
            rewritten_response="Corrected answer without fabricated claims.",
            detail="Hallucination markers detected in generated response.",
            processing_time_ms=21.3,
        )

        self.assertEqual(metadata["action"], "rewrite")
        self.assertEqual(metadata["threat_type"], "hallucination")
        self.assertEqual(metadata["rewritten_response"], "Corrected answer without fabricated claims.")

    def test_redact_all_masks_bearer_tokens_in_output_text(self):
        # Use an OPAQUE (non-sk) bearer token so this exercises the bearer_token
        # credential path specifically. (An sk-proj-… token is now ALSO recognized
        # as an API key — see test_redact_all_masks_modern_api_keys — and gets the
        # api-key mask first, which is the more precise redaction.)
        raw_text = "Authorization: Bearer eyJhbGciOiJIUzI1Ni1234567890OPAQUEjwtTOKENvalue"

        safe_text = redact_all(raw_text)

        self.assertNotIn("eyJhbGciOiJIUzI1Ni1234567890OPAQUEjwtTOKENvalue", safe_text)
        self.assertIn("[BEARER_TOKEN_REDACTED]", safe_text)

    def test_redact_all_masks_modern_api_keys(self):
        # N-CRED: modern hyphenated key formats (OpenAI sk-proj-…/sk-svcacct-…,
        # OpenRouter sk-or-v1-…) must be redacted out of OUTPUT text — the old
        # \bsk-[a-zA-Z0-9]{32,}\b pattern required an unbroken run and missed them.
        for raw_key in (
            "sk-proj-abcdefghij1234567890abcdefghij",
            "sk-or-v1-deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "sk-svcacct-ABCDEFghijkl1234567890mnopqr",
        ):
            safe_text = redact_all(f"The key is {raw_key} use it now.")
            self.assertNotIn(raw_key, safe_text)


if __name__ == "__main__":
    unittest.main()

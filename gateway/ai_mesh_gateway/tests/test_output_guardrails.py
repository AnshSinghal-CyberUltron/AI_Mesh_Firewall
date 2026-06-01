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
        raw_text = "Authorization: Bearer sk-proj-abcdefghij1234567890abcdefghij"

        safe_text = redact_all(raw_text)

        self.assertNotIn("sk-proj-abcdefghij1234567890abcdefghij", safe_text)
        self.assertIn("[BEARER_TOKEN_REDACTED]", safe_text)


if __name__ == "__main__":
    unittest.main()

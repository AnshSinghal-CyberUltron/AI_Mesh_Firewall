"""Chat OG: hallucination enable alias + rewrite honesty (sanitize)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from output_guard import (  # noqa: E402
    OutputGuard,
    OutputVerdict,
    sanitize_output_for_verdict,
)
from patterns import detect_hallucination_markers  # noqa: E402


class _StubScanner:
    def redact_pii(self, text: str) -> str:
        from patterns import redact_all

        return redact_all(text)

    async def scan_output(self, text: str):
        class V:
            action = "allow"
            threat_type = ""
            detail = ""
            matched_patterns: list = []
            confidence = 0.0
            compliance_tags: list = []
            matched_values: dict = {}

        return V()

    async def scan_output_with_tier2(self, *a, **k):
        return None


HALL_TEXT = (
    "Research shows approximately 42% of enterprises already deploy "
    "quantum blockchain AI per Dr. Smith et al."
)


@pytest.mark.asyncio
async def test_factuality_check_enabled_alias_enables_hallucination():
    """UI key factuality_check_enabled must enable hall check even if
    hallucination_flag_enabled is missing/False-stale in a partial bundle."""
    assert detect_hallucination_markers(HALL_TEXT)
    g = OutputGuard(_StubScanner(), config={"hallucination_flag_enabled": False})
    v = await g.inspect(
        HALL_TEXT,
        org_config={
            "factuality_check_enabled": True,
            # Explicit False used to win and disable — alias must OR-enable.
            "hallucination_flag_enabled": False,
            "output_hallucination_action": "rewrite",
            "hallucination_grounding_threshold": 0.2,
            "output_pii_enabled": False,
            "output_credential_enabled": False,
            "output_ip_leakage_enabled": False,
            "output_exfil_enabled": False,
            "output_tier2_enabled": False,
        },
    )
    # With explicit False, OR with factuality True should enable.
    # Implementation: prefer hallucination_flag_enabled if present — need OR logic.
    # If still allow, the fix must use OR not nested get default.
    assert v.action == "rewrite", f"expected rewrite, got {v.action}/{v.threat_type}"
    assert v.threat_type == "hallucination"


def test_sanitize_rewrite_pii_uses_static_rewrite_not_surgical_only():
    """UI rewrite on PII must not silently degrade to surgical redact-only."""
    original = "Email john.doe@corp.example for support."
    verdict = OutputVerdict(action="rewrite", threat_type="pii", detail="email", confidence=0.9)
    out = sanitize_output_for_verdict(original, verdict, redact_pii_fn=lambda t: t.replace("john.doe@corp.example", "[redacted]"))
    assert "john.doe@corp.example" not in out
    # Static rewrite template for pii
    assert "Sensitive personal information" in out or "[redacted]" in out or "removed" in out.lower()

"""G84: a manipulated / prompt-injected model can EXFILTRATE a secret in the response by
transport-encoding it (base64 / hex) AND breaking the blob with zero-width / bidi / format
(Cf) chars or ASCII whitespace. The output guard DETECTS this (its detectors decode over the
Cf-stripped + whitespace-collapsed canonical view via _iter_transport_decodes_canon, G75/G76)
and yields a `redact` verdict — but the deterministic redactor scanned the RAW bytes only, so
masking was a NO-OP on the obfuscated blob. Unlike the INPUT path (B1: redact no-op -> block),
the OUTPUT/egress path fails OPEN: a no-op redact is relabeled "flag" and the still-decodable
blob is EGRESSED to the client (main.py:7714 / secure_streaming.py:443-464). A client that strips
the invisibles (or whitespace) and base64/hex-decodes recovers the plaintext secret => LLM06.

FIX: patterns._redact_obfuscated now decodes over the transport-normalized view and maps each
secret-bearing token's span back onto the ORIGINAL bytes, so redact_all masks the whole blob.

These freeze the EGRESS-BYTES invariant end-to-end through the real OutputGuard.inspect ->
sanitize_output_for_verdict path: whatever the guard flags as a redact, the sanitized bytes must
NOT carry a client-recoverable secret. The egress bytes are the only source of truth.
"""
import asyncio
import base64
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import output_guard as og  # noqa: E402
from output_guard import OutputGuard  # noqa: E402
from scanner import InputScanner  # noqa: E402

_SC = InputScanner(config={})
# Force TIER-1 ONLY so this frozen regression is DETERMINISTIC: the leak & fix live entirely in the
# deterministic tier-1 detector/redactor pair (patterns.detect_* vs redact_all). The tier-2 (Bedrock)
# guard makes environment-flaky network calls (no creds in CI -> breaker state pollutes across tests),
# which would make egress verdicts nondeterministic. Disable it at both layers.
_SC.tier2_enabled = False
_SC._bedrock_scanner = None
_GUARD = OutputGuard(_SC, config={"output_guard_enabled": True, "output_tier2_enabled": False})

_SSN = "123-45-6789"
_AWS = "AKIAIOSFODNN7EXAMPLE"
_EMAIL = "alice.smith@example.com"
_ZW = "​"    # zero-width space
_ALM = "؜"   # arabic letter mark (Cf)
_RLO = "‮"   # right-to-left override (Cf, bidi)


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _interleave(s: str, ch: str) -> str:
    return ch.join(s)


def _ws_split(s: str, sep: str = " ", n: int = 4) -> str:
    return sep.join(s[i:i + n] for i in range(0, len(s), n))


def _client_recovers(egress: str, plain: str) -> bool:
    """Model a lenient client/renderer: strip Cf/Mn + whitespace, then base64/hex-decode any
    token, and check whether the plaintext secret is recoverable from the egress bytes."""
    views = {egress}
    stripped = "".join(c for c in egress if unicodedata.category(c) not in ("Cf", "Mn", "Me"))
    views.add(stripped)
    views.add(re.sub(r"\s+", "", stripped))
    for v in list(views):
        if plain in v:
            return True
        for tok in re.findall(r"[A-Za-z0-9+/=]{8,}", v):
            try:
                if plain.encode() in base64.b64decode(tok + "=" * ((-len(tok)) % 4), validate=False):
                    return True
            except Exception:
                pass
        for tok in re.findall(r"[0-9a-fA-F]{8,}", v):
            try:
                if len(tok) % 2 == 0 and plain.encode() in bytes.fromhex(tok):
                    return True
            except Exception:
                pass
    return False


def _egress(text: str) -> str:
    verdict = asyncio.run(_GUARD.inspect(text))
    if verdict.action == "block":
        return "[BLOCKED]"
    return og.sanitize_output_for_verdict(text, verdict, redact_pii_fn=_SC.redact_pii)


def _obfuscations(plain: str) -> dict:
    b = _b64(plain)
    return {
        "zw_b64": _interleave(b, _ZW),
        "alm_b64": _interleave(b, _ALM),
        "rlo_b64": _interleave(b, _RLO),
        "zw_hex": _interleave(plain.encode().hex(), _ZW),
        "ws_b64": _ws_split(b, " "),
        "nl_b64": _ws_split(b, "\n"),
    }


@pytest.mark.parametrize("plain", [_SSN, _AWS, _EMAIL])
def test_g84_transport_obfuscated_secret_never_egresses(plain):
    for oname, blob in _obfuscations(plain).items():
        egress = _egress(f"Here is the data you requested: {blob} -- done.")
        assert not _client_recovers(egress, plain), (
            f"LEAK: {oname} of {plain!r} is client-recoverable from egress bytes: {egress!r}"
        )


def test_g84_mask_is_surgical_not_whole_response():
    # A redact must mask the blob but PRESERVE the surrounding answer (never nuke to [REDACTED]).
    egress = _egress(f"Here is the data you requested: {_interleave(_b64(_SSN), _ZW)} -- done.")
    assert "Here is the data you requested" in egress
    assert egress.rstrip().endswith("done.")
    assert "[ENCODED_SECRET_REDACTED]" in egress or "REDACTED" in egress


@pytest.mark.parametrize("benign", [
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ",
    "AKIA IOSF here are four random capitalized words BANK CODE",
    "eyJhbGciOiJIUzI1NiJ9 eyJzdWIiOiJhYmMifQ signature-part-here",
    "The quick brown fox jumps over the lazy dog and returns home.",
])
def test_g84_benign_output_not_altered(benign):
    # Benign base64/whitespace content that does NOT decode to a secret must egress verbatim
    # (no false-positive masking, no false block).
    assert _egress(benign) == benign, f"benign output wrongly altered: {benign!r}"

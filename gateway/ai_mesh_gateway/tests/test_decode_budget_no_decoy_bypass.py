"""CHG-0060: the obfuscation decode scan must not be bypassable by decoy-padding.

The base64/hex/URL decode passes in ``_redact_obfuscated`` used a fixed token-COUNT
cap (base64/hex: 12; url: 32). A tool result could therefore hide an encoded secret
PAST the cap — ``<12 benign base64 blobs> <base64(secret)>`` — and the secret token
was never decoded, so it egressed verbatim (trivially recoverable). The cap is now a
decoded-BYTE budget sized to cover the whole ``_CANON_MAX_LEN``-bounded input, so every
encoded token within the scan window is decode-scanned; the budget still bounds total
work (decode-bomb safe).
"""
import base64
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from patterns import _CANON_MAX_LEN, redact_all  # noqa: E402


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def test_base64_secret_hidden_past_decoys_is_masked():
    decoys = " ".join(_b64(f"decoyvalue{i:03d}") for i in range(20))  # > old cap of 12
    secret = _b64("john.doe@example.com")
    red = redact_all(decoys + " " + secret)
    assert "[ENCODED_SECRET_REDACTED]" in red
    assert secret not in red


def test_hex_secret_hidden_past_decoys_is_masked():
    decoys = " ".join(f"decoyval{i:03d}".encode().hex() for i in range(20))
    hsecret = "john.doe@example.com".encode().hex()
    red = redact_all(decoys + " " + hsecret)
    assert "[ENCODED_SECRET_REDACTED]" in red
    assert hsecret not in red


def test_url_encoded_secret_hidden_past_decoys_is_masked():
    decoys = " ".join(f"k{i}=v%2{i % 10}" for i in range(40))  # > old url cap of 32
    secret = "email=" + urllib.parse.quote("john.doe@example.com", safe="")
    red = redact_all(decoys + " " + secret)
    assert "[ENCODED_SECRET_REDACTED]" in red
    assert "john.doe" not in red


def test_many_decoys_within_scan_window_still_catches_secret():
    # ~850 tokens (~17.8KB) keeps the secret INSIDE the _CANON_MAX_LEN window
    decoys = " ".join(_b64(f"decoyvalue{i:04d}") for i in range(850))
    secret = _b64("john.doe@example.com")
    text = decoys + " " + secret
    assert len(text) < _CANON_MAX_LEN
    red = redact_all(text)
    assert "[ENCODED_SECRET_REDACTED]" in red
    assert secret not in red


def test_benign_decoys_not_falsely_masked_as_encoded_secret():
    # many base64 tokens that decode to non-sensitive text -> the decode pass must not
    # tag any of them as an encoded secret (no false positive from scanning them all)
    benign = " ".join(_b64(f"benignpad{i:03d}") for i in range(60))
    assert "[ENCODED_SECRET_REDACTED]" not in redact_all(benign)


def test_plain_and_single_encoded_still_work():
    assert "john" not in redact_all("contact john@example.com")            # plain PII
    assert "[ENCODED_SECRET_REDACTED]" in redact_all("blob " + _b64("john@example.com"))


def test_decode_scan_is_bounded_fast_on_large_input():
    # worst case: a full scan-window of base64 tokens must complete quickly (byte budget
    # + input cap keep it bounded; guards against a decode-scan DoS regression)
    big = " ".join(_b64(f"benignpad{i:04d}") for i in range(850))
    secret = _b64("john.doe@example.com")
    t = time.time()
    red = redact_all(big + " " + secret)
    elapsed = time.time() - t
    assert elapsed < 2.0, f"decode scan too slow: {elapsed:.2f}s"
    assert "[ENCODED_SECRET_REDACTED]" in red


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

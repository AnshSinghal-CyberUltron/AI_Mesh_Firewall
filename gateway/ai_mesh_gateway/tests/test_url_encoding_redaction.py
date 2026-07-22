"""CHG-0056 regression: redact_all must catch PERCENT/URL-encoded PII/secrets.

redact_all already de-obfuscated base64/hex-encoded PII/secrets (the G2 pass), but it
did NOT URL-decode — so a %XX-encoded email in a URL query param
(``john.doe%40example.com``) or a %-encoded SSN broke the raw patterns and egressed
(the value is trivially recoverable by any URL parser / by eye). The new percent-decode
pass masks the whole encoded token when its URL-decoded form matches PII/secret, guarded
so benign percent text (``50%20off``, ``C%3A%5Cpath``) is untouched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import base64  # noqa: E402

import pytest  # noqa: E402

from patterns import redact_all  # noqa: E402


@pytest.mark.parametrize(
    ("text", "leaked_fragment"),
    [
        ("visit https://app/u?email=john.doe%40example.com&x=1", "john.doe"),
        ("dash-encoded ssn 123%2d45%2d6789 on file", "123-45-6789"),
        ("fully %31%32%33%2d%34%35%2d%36%37%38%39 encoded", "123-45-6789"),
        ("email%40 mixed john.doe%40corp.example.com end", "corp.example.com"),
    ],
)
def test_url_encoded_pii_masked(text, leaked_fragment):
    red = redact_all(text)
    assert "[ENCODED_SECRET_REDACTED]" in red
    assert leaked_fragment not in red          # the decoded PII fragment does not survive


@pytest.mark.parametrize(
    "text",
    [
        "50%20off sale today",          # -> "50 off", no PII
        r"windows path C%3A%5Ctemp%5Cx",  # -> "C:\temp\x", no PII
        "download 95% complete",        # bare % with no valid escape
        "pagination ?p=2%2C3 next",     # -> "2,3", no PII
        "just some normal text here",   # no percent at all
    ],
)
def test_benign_percent_text_not_masked(text):
    assert redact_all(text) == text


def test_base64_and_hex_obfuscation_still_work():
    b = "blob " + base64.b64encode(b"john.doe@example.com").decode()
    h = "hex " + "john.doe@example.com".encode().hex()
    assert "[ENCODED_SECRET_REDACTED]" in redact_all(b)
    assert "[ENCODED_SECRET_REDACTED]" in redact_all(h)


def test_plain_pii_still_masked_normally():
    red = redact_all("email john.doe@example.com and ssn 123-45-6789")
    assert "john.doe@example.com" not in red
    assert "123-45-6789" not in red
    assert "[ENCODED_SECRET_REDACTED]" not in red   # plain PII uses the normal maskers


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

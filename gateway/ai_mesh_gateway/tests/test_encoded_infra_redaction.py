"""CHG-0058 regression: redact_all must catch base64/hex/URL-encoded INTERNAL network
addresses (internal IPv4, internal hostname, internal URL).

redact_all already de-obfuscated base64/hex/URL-encoded PII/secrets (CHG-0056 + the G2
pass), but the decode branches in ``_redact_obfuscated`` checked only
``_detect_pii_core`` / ``_detect_secrets_core`` — NOT ``detect_ip_leakage``. So an
internal address hidden inside an encoded blob (``base64("10.1.2.3")`` in a tool result,
or a %-encoded ``http://192.168.50.123:8080/admin``) survived scrubbing and egressed —
the value is trivially recoverable by any base64/URL decoder. The decode branches now
also fire on decoded INTERNAL network addresses, masking the whole encoded token as
``[ENCODED_SECRET_REDACTED]``.

Scoped to the NETWORK keys (``internal_ipv4`` / ``internal_hostname`` / ``internal_url``)
only — file-path keys are deliberately excluded to match ``redact_all``'s own masking
scope and to avoid false positives on benign encoded paths. The ``detect_ip_leakage``
example-address carve-out (RFC-5737 / textbook ``192.168.0.1`` etc.) is preserved: those
are NOT masked even when encoded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import base64  # noqa: E402
import urllib.parse  # noqa: E402

import pytest  # noqa: E402

from patterns import redact_all  # noqa: E402


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


@pytest.mark.parametrize(
    "addr",
    [
        "10.1.2.3",                              # internal_ipv4 (RFC-1918)
        "172.16.9.44",                           # internal_ipv4 (RFC-1918)
        "db.internal:5432",                      # internal_hostname (.internal)
        "postgres.svc.cluster.local",            # internal_hostname (k8s)
        "http://192.168.50.123:8080/admin",      # internal_url
    ],
)
def test_base64_encoded_internal_addr_masked(addr):
    red = redact_all("tool-result blob " + _b64(addr) + " trailer")
    assert "[ENCODED_SECRET_REDACTED]" in red
    assert _b64(addr) not in red                 # the encoded token does not survive
    # and no fragment of the raw address leaks through in decoded form
    assert addr.split(":")[0].split("/")[0] not in red


@pytest.mark.parametrize(
    "addr",
    [
        "10.1.2.3",
        "postgres.svc.cluster.local",
    ],
)
def test_hex_encoded_internal_addr_masked(addr):
    red = redact_all("hex " + addr.encode().hex())
    assert "[ENCODED_SECRET_REDACTED]" in red


def test_url_encoded_internal_url_masked():
    enc = urllib.parse.quote("http://192.168.50.123:8080/admin", safe="")
    red = redact_all("callback url=" + enc)
    assert "[ENCODED_SECRET_REDACTED]" in red
    assert "192.168.50.123" not in red


@pytest.mark.parametrize(
    "addr",
    [
        "192.168.0.1",     # textbook / example carve-out in detect_ip_leakage
        "10.0.0.1",        # textbook / example carve-out
    ],
)
def test_encoded_example_addresses_not_masked(addr):
    # The example-address carve-out must survive the decode path too: encoding a
    # textbook address must NOT trigger a false redaction.
    text = "docs example " + _b64(addr) + " here"
    assert redact_all(text) == text


def test_encoded_file_path_not_over_redacted():
    # File-path leakage keys are intentionally OUT of scope for the encoded-infra
    # check (they are not masked by redact_all in plain form either), so an encoded
    # unix path must not be falsely masked as an internal address.
    enc = _b64("/home/user/secret/config.yaml")
    text = "path " + enc
    assert redact_all(text) == text


def test_plain_internal_addr_still_masked_normally():
    # Non-encoded internal addresses continue to use the normal IP-leakage maskers,
    # NOT the encoded-token tag.
    red = redact_all("host 10.1.2.3 reachable")
    assert "10.1.2.3" not in red
    assert "[ENCODED_SECRET_REDACTED]" not in red


def test_base64_pii_and_secret_still_masked():
    # No regression to the pre-existing encoded-PII/secret behaviour.
    assert "[ENCODED_SECRET_REDACTED]" in redact_all("x " + _b64("john.doe@example.com"))
    assert "[ENCODED_SECRET_REDACTED]" in redact_all("y " + _b64("AKIAIOSFODNN7EXAMPLE"))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

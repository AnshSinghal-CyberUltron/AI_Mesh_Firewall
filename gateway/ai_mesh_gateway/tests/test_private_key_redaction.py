"""CHG-0054 regression: redact_all must mask the ENTIRE PEM private-key block.

Before the fix, the ``private_key_header`` pattern matched only the ``-----BEGIN-----``
header line, so ``redact_all`` replaced the header with ``[PRIVATE_KEY]`` while the
base64 key BODY (the actual secret material) egressed intact — and the old pattern
only handled RSA keys, so EC/DSA/OPENSSH keys were not matched at all (full key
egressed). ``detect_pii`` DOES flag ``private_key_header``, so a tool RESULT containing
a private key is scanned and "redacted" — the redaction just wasn't removing the secret.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patterns import detect_pii, redact_all  # noqa: E402

_RSA = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA7Yn3kLmQ9fJ2xVwPqRsTuVwXyZ0123456789abcdefghijkl\n"
    "mnopQRSTUVWXYZ0123456789+/aBcDeFgHiJkLmNoPqRsTuVwXyZsecretmaterial\n"
    "-----END RSA PRIVATE KEY-----"
)


def test_rsa_private_key_body_fully_masked():
    assert "private_key_header" in detect_pii(_RSA)      # detected as a secret ...
    red = redact_all(_RSA)
    assert "MIIEpAIBAAKCAQEA" not in red                 # ... and the body material is gone
    assert "secretmaterial" not in red
    assert red.count("[PRIVATE_KEY]") == 1               # whole block collapsed to one tag


def test_ec_private_key_masked_old_pattern_missed_it():
    # The old RSA-only pattern did NOT match EC keys at all -> the whole key egressed.
    ec = (
        "cfg:\n-----BEGIN EC PRIVATE KEY-----\n"
        "MHcCAQEEIabcECKEYMATERIALxyz012345+/=\n"
        "-----END EC PRIVATE KEY-----\nend"
    )
    red = redact_all(ec)
    assert "ECKEYMATERIAL" not in red
    assert "[PRIVATE_KEY]" in red
    assert "cfg:" in red and "end" in red                # surrounding text preserved


def test_openssh_private_key_masked():
    key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmVSECRETXYZ\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    assert "SECRETXYZ" not in redact_all(key)


def test_truncated_private_key_body_masked():
    # A BEGIN with no END (truncated) must still not leak the base64 body.
    trunc = "-----BEGIN RSA PRIVATE KEY-----\nMIIEtruncSECRETbodyBASE64material0123\n"
    assert "truncSECRETbody" not in redact_all(trunc)


def test_prose_mentioning_private_key_not_redacted():
    # No false positive on prose that merely references a private key.
    prose = "The service loads a private key from the vault before boot."
    assert redact_all(prose) == prose


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

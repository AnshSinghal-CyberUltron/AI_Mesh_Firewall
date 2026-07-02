"""CHG-0072: a second adversarial redact_all sweep found more distinctive-prefix
provider tokens egressing UNMASKED — DigitalOcean, Shopify, Square, Databricks,
HashiCorp Vault, Figma, Telegram bot, PyPI, Linear, Mailgun — plus AWS STS temporary
access-key IDs (``ASIA…``) which the ``aws_access_key`` pattern (AKIA-only) missed.
All are now detected (drives the tier-1 redact/block decision) + tagged SECRET + masked.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from patterns import (  # noqa: E402
    detect_pii,
    detect_secrets,
    get_compliance_tags,
    redact_all,
)

# FAKE / example values (structurally valid, not real).
_SECRET_CASES = {
    "digitalocean_pat": "dop_v1_" + "a1b2c3d4" * 8,
    "shopify_token": "shpat_abcdef1234567890abcdef1234567890",
    "square_token": "sq0atp-abcdefghijklmnopqrstuv",
    "databricks_token": "dapiabcdef1234567890abcdef1234567890",
    "hashicorp_vault_token": "hvs.abcdefghijklmnopqrstuvwxyz0123456789",
    "figma_token": "figd_abcdef1234567890-abcdefghijklmnopqrstuv",
    "telegram_bot_token": "123456789:AAEabcdefghijklmnopqrstuvwxyz012345678",
    "pypi_token": "pypi-AgEIcHlwaS5vcmcabcdefghijklmnopqrstuvwxyz",
    "linear_api_key": "lin_api_abcdef1234567890abcdef1234567890",
    "mailgun_key": "key-abcdef1234567890abcdef1234567890",
}


@pytest.mark.parametrize(("key", "value"), list(_SECRET_CASES.items()))
def test_new_secret_detected_tagged_masked(key, value):
    found = detect_secrets(value)
    assert key in found, f"{key} not detected: {found}"
    assert "SECRET" in get_compliance_tags(list(found.keys()))
    assert value not in redact_all(value)


def test_aws_sts_temporary_key_asia_now_caught():
    # ASIA (STS temporary access-key id) lives in PII_PATTERNS -> detect_pii; AKIA still works
    asia = "ASIAABCDEFGHIJKLMNOP"
    assert "aws_access_key" in detect_pii(asia)
    assert asia not in redact_all(asia)
    assert "aws_access_key" in detect_pii("AKIAIOSFODNN7EXAMPLE")  # regression


def test_telegram_token_masked_in_api_url():
    url = "https://api.telegram.org/bot123456789:AAEabcdefghijklmnopqrstuvwxyz012345678/sendMessage"
    assert "123456789:AAE" not in redact_all(url)


@pytest.mark.parametrize(
    "benign",
    [
        "the api-key-value config option",     # key- but not 32 hex
        "monkey-see monkey-do playtime",       # key- substring
        "shape shpattern square dance sq0",    # prefix-ish substrings, not the format
        "figure this out, figd is short",
        "robot12345 and chatbot999 chatting",  # digits but no :AA token
        "just plain prose with no secrets",
    ],
)
def test_no_false_positive(benign):
    assert redact_all(benign) == benign


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

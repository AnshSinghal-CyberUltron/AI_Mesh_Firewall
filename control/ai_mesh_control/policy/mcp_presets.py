"""
MCP guardrail PII/secret presets.

A deterministic, regex-based catalogue of common sensitive-data matchers
("presets") that an operator can attach to an MCP policy *rule* without
writing a regex by hand. The SAME catalogue is used by:

  * the dry-run simulator (`POST /api/policies/test/`)
  * live MCP tool-call enforcement (`mcp_connector.views`)

so what the operator previews in the Simulator is exactly what the
gateway enforces — no ML/Presidio drift between preview and production.

Each preset exposes:

  key          stable identifier stored in ``rule.condition["preset"]``
  label        human label for the UI dropdown
  description  one-line help text
  regex        the IGNORECASE pattern used to find candidates
  validator    optional callable(str) -> bool applied to each regex match
               (e.g. Luhn for credit cards) to suppress false positives
  replacement  default redaction placeholder for this preset

Operators always retain full control: a rule may instead use a custom
``regex`` or ``keywords`` (handled by the engine directly). Presets are a
convenience layer on top of that, never a replacement for it.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from policy.constants import DEFAULT_REDACTION_PLACEHOLDER


def luhn_valid(value: str) -> bool:
    """Return True if ``value``'s digits pass the Luhn checksum and the
    digit count is a plausible payment-card length (13–19).

    Used to keep the credit-card preset from flagging arbitrary long
    numbers (phone numbers, order ids, etc.). A 10-digit string such as
    ``8929554991`` therefore does NOT match the credit-card preset.
    """
    digits = [int(c) for c in re.sub(r"[^\d]", "", value)]
    if not (13 <= len(digits) <= 19):
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# Ordered so the UI dropdown reads naturally. ``regex`` patterns are
# applied with re.IGNORECASE by the engine.
_PRESETS: dict[str, dict[str, Any]] = {
    "credit_card": {
        "label": "Credit card number",
        "description": "13–19 digit payment card numbers, validated with the Luhn checksum.",
        "regex": r"\b(?:\d[ -]*?){13,19}\b",
        "validator": luhn_valid,
        "replacement": "[REDACTED_CARD]",
    },
    "us_ssn": {
        "label": "US Social Security Number",
        "description": "US SSN in 123-45-6789 form (dashes or spaces).",
        "regex": r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",
        "replacement": "[REDACTED_SSN]",
    },
    "email": {
        "label": "Email address",
        "description": "RFC-style email addresses.",
        "regex": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        "replacement": "[REDACTED_EMAIL]",
    },
    "phone": {
        "label": "Phone number",
        "description": "International / US phone numbers (10+ digits with separators).",
        "regex": r"\b(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}\b",
        "replacement": "[REDACTED_PHONE]",
    },
    "ip_address": {
        "label": "IP address (IPv4)",
        "description": "Dotted-quad IPv4 addresses.",
        "regex": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
        "replacement": "[REDACTED_IP]",
    },
    "api_key": {
        "label": "API key / secret token",
        "description": "Common secret formats: sk-/pk-/rk- prefixes or long opaque tokens.",
        "regex": r"\b(?:sk|pk|rk|api|key|tok|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b",
        "replacement": "[REDACTED_SECRET]",
    },
    "aws_access_key": {
        "label": "AWS access key ID",
        "description": "AWS access key IDs (AKIA/ASIA + 16 chars).",
        "regex": r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b",
        "replacement": "[REDACTED_AWS_KEY]",
    },
    "iban": {
        "label": "IBAN / bank account",
        "description": "International Bank Account Numbers.",
        "regex": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
        "replacement": "[REDACTED_IBAN]",
    },
    "jwt": {
        "label": "JWT / bearer token",
        "description": "JSON Web Tokens (three base64url segments).",
        "regex": r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
        "replacement": "[REDACTED_JWT]",
    },
    "private_key": {
        "label": "Private key block",
        "description": "PEM private key headers (RSA/EC/OpenSSH).",
        "regex": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
        "replacement": "[REDACTED_PRIVATE_KEY]",
    },
}


def get_preset(key: str | None) -> dict[str, Any] | None:
    """Return the preset definition for ``key`` (case-insensitive) or None."""
    if not key or not isinstance(key, str):
        return None
    return _PRESETS.get(key.strip().lower())


def preset_regex(key: str | None) -> str | None:
    """Return the regex pattern for a preset key, or None if unknown."""
    preset = get_preset(key)
    return preset.get("regex") if preset else None


def preset_validator(key: str | None) -> Callable[[str], bool] | None:
    """Return the optional per-match validator for a preset key, or None."""
    preset = get_preset(key)
    return preset.get("validator") if preset else None


def preset_replacement(key: str | None) -> str:
    """Return the default redaction placeholder for a preset key."""
    preset = get_preset(key)
    if preset and preset.get("replacement"):
        return preset["replacement"]
    return DEFAULT_REDACTION_PLACEHOLDER


def list_presets() -> list[dict[str, str]]:
    """Return a JSON-serializable catalogue for the UI dropdown.

    Excludes the (non-serializable) validator callable and the raw regex,
    exposing only what the frontend needs to render choices. The regex is
    intentionally withheld so the catalogue stays an opaque, server-owned
    contract.
    """
    return [
        {
            "key": key,
            "label": preset["label"],
            "description": preset["description"],
        }
        for key, preset in _PRESETS.items()
    ]

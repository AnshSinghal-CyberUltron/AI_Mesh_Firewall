"""MCP guardrail PII/secret presets — shared by control plane and gateway.

Single catalogue for simulator, MCPToolCallView, and gateway Tier-1 scanning.
"""

from __future__ import annotations

import re
from typing import Any, Callable

DEFAULT_REDACTION_PLACEHOLDER = "[REDACTED]"


def luhn_valid(value: str) -> bool:
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
    if not key or not isinstance(key, str):
        return None
    return _PRESETS.get(key.strip().lower())


def preset_regex(key: str | None) -> str | None:
    preset = get_preset(key)
    return preset.get("regex") if preset else None


def preset_validator(key: str | None) -> Callable[[str], bool] | None:
    preset = get_preset(key)
    return preset.get("validator") if preset else None


def preset_replacement(key: str | None) -> str:
    preset = get_preset(key)
    if preset and preset.get("replacement"):
        return preset["replacement"]
    return DEFAULT_REDACTION_PLACEHOLDER


def list_presets() -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "label": preset["label"],
            "description": preset["description"],
        }
        for key, preset in _PRESETS.items()
    ]

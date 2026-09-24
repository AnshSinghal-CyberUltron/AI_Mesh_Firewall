"""Sanitize C2 captures at write time. Never un-sanitize later."""

from __future__ import annotations

import re

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_AKIA = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")
_SECRET_HEADER = frozenset({"authorization", "x-api-key", "cookie", "x-openai-key"})


def sanitize_text(text: str) -> str:
    out = _SSN.sub("[SSN]", text)
    out = _AKIA.sub("[AWS_KEY]", out)
    out = _EMAIL.sub("[EMAIL]", out)
    return _BEARER.sub(r"\1[REDACTED]", out)


def sanitize_headers(headers: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    cleaned: list[tuple[str, str]] = []
    for name, value in headers:
        key = name.lower()
        if key in _SECRET_HEADER:
            cleaned.append((name, "[REDACTED]"))
        else:
            cleaned.append((name, sanitize_text(value)))
    return tuple(cleaned)

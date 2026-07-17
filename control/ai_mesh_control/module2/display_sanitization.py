"""Remove legacy internal module numbering from customer-visible incident data."""

from __future__ import annotations

import re
from typing import Any


_MODULE_26_RE = re.compile(r"\bM\s*2\." + r"6\b", re.IGNORECASE)
_LEGACY_KEY_PREFIX_RE = re.compile(r"\bzs_" + r"m26\b", re.IGNORECASE)


def sanitize_incident_text(value: Any) -> Any:
    """Return text without legacy module numbering; preserve non-string values."""
    if not isinstance(value, str):
        return value
    cleaned = _MODULE_26_RE.sub("Incidents", value)
    cleaned = _LEGACY_KEY_PREFIX_RE.sub("zs_incidents", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def sanitize_incident_value(value: Any) -> Any:
    """Recursively sanitize strings in JSON-compatible values."""
    if isinstance(value, dict):
        return {key: sanitize_incident_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_incident_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_incident_value(item) for item in value)
    return sanitize_incident_text(value)

"""
Redaction helper: apply redaction hints to text and return redacted text.
Used when engine returns action=redact.
"""

import functools
import re
from typing import Any

from policy.constants import DEFAULT_REDACTION_PLACEHOLDER


@functools.lru_cache(maxsize=256)
def _compile_cached(pattern: str) -> re.Pattern[str]:
    """Compile and cache regex patterns for performance."""
    return re.compile(pattern, re.IGNORECASE)


def _mask_email(match: re.Match[str]) -> str:
    value = match.group(0)
    local, domain_full = value.split("@", 1)
    domain_parts = domain_full.rsplit(".", 1)
    domain_name = domain_parts[0]
    tld = domain_parts[1] if len(domain_parts) > 1 else "com"
    return f"{local[:1] or '*'}***@{domain_name[:1] or '*'}***.{tld}"


def _mask_credit_card(match: re.Match[str]) -> str:
    digits = re.sub(r"[^\d]", "", match.group(0))
    return f"****-****-****-{digits[-4:]}"


def _mask_ssn(match: re.Match[str]) -> str:
    value = match.group(0)
    return f"***-**-{value[-4:]}"


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"[^\d]", "", match.group(0))
    return f"***-***-{digits[-4:]}"


def _smart_substitute(result: str, regex_pattern: str, replacement: str) -> str:
    try:
        compiled = _compile_cached(regex_pattern)
    except re.error:
        return result

    normalized = regex_pattern.replace("\\", "")
    if "@" in normalized and "[A-Za-z" in regex_pattern:
        return compiled.sub(_mask_email, result)
    if "d{3}-d{2}-d{4}" in normalized:
        return compiled.sub(_mask_ssn, result)
    if "d{4}[s-]?d{4}[s-]?d{4}[s-]?d{4}" in normalized:
        return compiled.sub(_mask_credit_card, result)
    if "d{3}" in normalized and "d{4}" in normalized and "(?" in regex_pattern:
        return compiled.sub(_mask_phone, result)
    return compiled.sub(replacement, result)


def apply_redaction(
    text: str,
    redaction_hints: list[dict[str, Any]],
    *,
    placeholder: str = DEFAULT_REDACTION_PLACEHOLDER,
) -> str:
    """
    Apply redaction hints to text. Each hint can have:
    - config: dict with 'regex' (pattern to replace) or 'keywords' (list to mask),
      and optional 'replacement' (default placeholder).
    - rule condition can be used to derive regex/keywords if not in config.

    Returns redacted text.
    """
    if not text or not redaction_hints:
        return text

    result = text
    for hint in redaction_hints:
        config = hint.get("config") or {}
        condition = hint.get("condition") or {}
        repl = config.get("replacement") or placeholder

        regex_pattern = (
            config.get("regex")
            or config.get("pattern")
            or condition.get("regex")
            or condition.get("pattern")
        )
        if regex_pattern:
            result = _smart_substitute(result, regex_pattern, repl)

        keywords = (
            config.get("keywords")
            or config.get("keywords_list")
            or condition.get("keywords")
            or condition.get("keywords_list")
            or []
        )
        for kw in keywords:
            if not isinstance(kw, str):
                continue
            pattern = re.escape(kw)
            try:
                compiled = _compile_cached(rf"\b{pattern}\b")
                result = compiled.sub(repl, result)
            except re.error:
                continue

    return result

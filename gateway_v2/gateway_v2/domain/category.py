"""Single threat taxonomy. One spelling per concept."""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    PII = "pii"
    SECRET = "secret"
    CREDENTIAL = "credential"
    COMMAND = "command"
    TOXICITY = "toxicity"
    EXFIL = "exfil"
    DOS = "dos"
    # GW33 output parity: one spelling each (D-1). ``EXFIL`` is reused for exfiltration channels.
    IP_LEAK = "ip_leak"
    HALLUCINATION = "hallucination"
    CANARY = "canary"


def parse_category(value: object) -> Category:
    """Reject raw strings that are not a Category member, including the v1 alias."""
    if isinstance(value, Category):
        return value
    if isinstance(value, str):
        try:
            return Category(value)
        except ValueError as exc:
            raise TypeError(f"unknown category {value!r}") from exc
    raise TypeError("category must be a Category")

"""The ONE vocabulary (rb.md L2155, L2193): enums shared by every layer."""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    PII = "pii"
    SECRET = "secret"


class Mode(StrEnum):
    OFF = "off"
    MONITOR = "monitor"
    ENFORCE = "enforce"


class Action(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


class FailurePosture(StrEnum):
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


class RuleScope(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    BOTH = "both"


class StreamingMode(StrEnum):
    INCREMENTAL = "incremental"
    STRICT_WITHHOLD = "strict_withhold"


class FindingStatus(StrEnum):
    EXECUTED = "executed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


class Disposition(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REDACT = "redact"
    BLOCK = "block"


class Phase(StrEnum):
    INPUT = "input"
    OUTPUT = "output"

"""Deterministic PII / secret / injection-signal detectors (GW09, §10.9).

The pure, in-process deterministic detector package behind the shipped
``gateway_v2.detect.base.Detector`` protocol (GW08). It ships ONE
canonicalization pass (:mod:`canonicalize`), ONE versioned/hashed ruleset
(:mod:`patterns`), ONE combined-alternation matcher (:mod:`matcher`), ONE
byte-verified redactor + reusable ``verify`` (:mod:`redactor`), and the concrete
``Detector``-protocol classes (:mod:`detectors`). This module re-exports the
public surface the resolver (GW07), dispatch re-verify (GW11), and tests consume
-- the detector classes, the ``canonicalize`` / ``scan`` / ``redact`` /
``verify`` / ``ruleset_version`` functions, and the frozen value types.

Layering: imports only sibling ``gateway_v2.detect.deterministic.*`` modules,
which themselves import only ``gateway_v2.domain`` + ``gateway_v2.detect.*``
(R8.7).
"""

from gateway_v2.detect.deterministic.canonicalize import (
    DEFAULT_BUDGET,
    REDACTION_PLACEHOLDER,
    BudgetExceeded,
    DecodeBudget,
    SpanMap,
    canonicalize,
)
from gateway_v2.detect.deterministic.detectors import (
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
)
from gateway_v2.detect.deterministic.matcher import RawMatch, scan
from gateway_v2.detect.deterministic.patterns import (
    RULESET,
    PatternKind,
    PatternSpec,
    ruleset_version,
)
from gateway_v2.detect.deterministic.redactor import RedactResult, redact, verify

__all__ = (
    "DEFAULT_BUDGET",
    "REDACTION_PLACEHOLDER",
    "RULESET",
    "BudgetExceeded",
    "DecodeBudget",
    "InjectionSignalDetector",
    "PatternKind",
    "PatternSpec",
    "PiiDetector",
    "RawMatch",
    "RedactResult",
    "SecretDetector",
    "SpanMap",
    "canonicalize",
    "redact",
    "ruleset_version",
    "scan",
    "verify",
)

"""Concrete Detector-protocol classes (GW09, §10.9, R1).

These are the deterministic PII / secret / injection-signal detectors GW09
ships (design "detectors.py"). Each is a thin, PURE wiring of the three sibling
primitives built in Tasks 1-3: ONE :func:`canonicalize` pass, ONE
:func:`scan` over the canonical text, then a map of each confirmed match back
to an ORIGINAL byte :class:`~gateway_v2.domain.finding.Span` via the
:class:`~gateway_v2.detect.deterministic.canonicalize.SpanMap`. Every detector
emits ``Finding[]`` only -- it never mutates ``text`` (R1.4), never decides
disposition, never raises HTTP, and runs in-process with no ``GuardBackend``
(R1.5). Each structurally satisfies the shipped
``gateway_v2.detect.base.Detector`` ``@runtime_checkable`` protocol (R1.2),
exposing ``detector_version`` and ``required_capabilities`` (R1.3).

Shared canonicalization, per-family emission. The three families share the one
canonicalization + matcher pass (the card: "detectors" plural, one
canonicalization). Each concrete detector filters ``scan`` output to the
patterns its family OWNS (``RULESET[name].detector_name == self._family``) and
emits one Finding per owned match.

Budget note (important). The ``DetectorContext.budget`` the protocol passes is a
``gateway_v2.detect.guard.backend.Budget`` -- a REMAINING-TIME budget
(``remaining_s``) for a guard-backed invocation (C30). It is NOT a
:class:`~gateway_v2.detect.deterministic.canonicalize.DecodeBudget`, which
bounds canonicalization expansion COST and is a different concept entirely. The
two are deliberately NOT conflated: canonicalization uses the module-level
``canonicalize.DEFAULT_BUDGET`` (the declared Decode_Budget) for its cheap
pre-check, and the guard ``Budget`` is irrelevant to a pure in-process
deterministic detector (there is no backend round-trip to time-bound). So
``context.budget`` is accepted by the protocol signature but is not used to
drive the decode; the decode is bounded by ``DEFAULT_BUDGET``.

Status + confidence (honouring ``Finding.__post_init__``). A deterministic
regex confirm is EXACT, so a match is emitted as ``status=EXECUTED`` with the
declared confidence constant :data:`_EXECUTED_CONFIDENCE` (``1.0`` -- a
DETECTION confidence, not a capacity value, R8.4) since ``__post_init__``
requires a non-``None`` confidence for ``EXECUTED``. When canonicalization
rejects on the Decode_Budget the detector cannot execute, so it emits a single
``status=UNAVAILABLE`` Finding with ``confidence=None`` (R1.6 / R2.5 -- never an
empty tuple presented as clean; ``__post_init__`` requires ``confidence is
None`` for any non-``EXECUTED`` status). ``evidence`` is always ``None`` -- the
spans + ``detector_version`` locate a finding; the audit layer keeps offsets,
never the raw matched value.

Injection/command demotion (R6, the §10.2.2 behaviour change). The
:class:`InjectionSignalDetector` emits ``PROMPT_INJECTION`` / ``JAILBREAK`` /
``COMMAND`` findings as a SIGNAL only -- a Finding, never a terminal block (a
detector cannot decide disposition anyway). The plan/resolver decides the
action. Because the ruleset carries NO bare backtick-pair command pattern, a
benign inline `` `code` `` prompt produces no finding.

Layering: this module imports only ``gateway_v2.domain``, the GW08
``gateway_v2.detect.base`` protocol, and the sibling GW09 modules
(``canonicalize`` / ``patterns`` / ``matcher``) -- no higher layer (R8.7). It
mirrors the house idiom: ``from __future__ import annotations``, frozen
``@dataclass(frozen=True, slots=True)``, a tuple ``__all__``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Final

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.canonicalize import (
    DEFAULT_BUDGET,
    BudgetExceeded,
    SpanMap,
    canonicalize,
)
from gateway_v2.detect.deterministic.matcher import RawMatch, scan
from gateway_v2.detect.deterministic.patterns import RULESET, ruleset_version
from gateway_v2.domain import Category, Finding, FindingStatus

__all__ = (
    "InjectionSignalDetector",
    "PiiDetector",
    "SecretDetector",
)

#: The confidence a deterministic (exact) regex confirm carries. A DETECTION
#: confidence, NOT a capacity value (R8.4): ``__post_init__`` requires a
#: non-``None`` confidence for an ``EXECUTED`` finding, and an exact match is
#: full confidence. A module-level named constant, never a dataclass field.
_EXECUTED_CONFIDENCE: Final = 1.0


@dataclass(frozen=True, slots=True)
class _DeterministicBase:
    """Shared canonicalize -> scan -> Finding[] wiring for one family.

    A frozen slotted dataclass carrying no capacity field (R8.4). Subclasses
    set two class attributes: ``_family`` -- the ``PatternSpec.detector_name``
    this detector OWNS (used to filter ``scan`` output) -- and ``_detector_id``
    / ``_unavailable_category`` -- the stable ``Finding.detector`` id and the
    representative category reported on an ``UNAVAILABLE`` finding.
    """

    #: The ``PatternSpec.detector_name`` family this detector owns. Overridden
    #: by each concrete subclass; the base owns nothing.
    _family: ClassVar[str] = ""
    #: The stable ``Finding.detector`` id for this family.
    _detector_id: ClassVar[str] = "deterministic"
    #: A representative category reported on an UNAVAILABLE finding.
    _unavailable_category: ClassVar[Category] = Category.PII

    required_capabilities: frozenset[str] = field(default_factory=frozenset)

    @property
    def detector_version(self) -> str:
        """The GW08 ``detector_version``: a deterministic hash of the ruleset (R8.3)."""
        return ruleset_version()

    def _owns(self, match: RawMatch) -> bool:
        """Whether ``match``'s pattern belongs to THIS detector's family."""
        return RULESET[match.pattern_name].detector_name == self._family

    def _unavailable(self) -> Finding:
        """A single cannot-execute Finding (R1.6 / R2.5 -- never empty-as-clean)."""
        return Finding(
            detector=self._detector_id,
            detector_version=ruleset_version(),
            category=self._unavailable_category,
            status=FindingStatus.UNAVAILABLE,
            confidence=None,
            spans=(),
            evidence=None,
        )

    def _finding(self, match: RawMatch, span_map: SpanMap) -> Finding:
        """One EXECUTED Finding for an owned match, with an ORIGINAL byte Span."""
        original_span = span_map.to_original(match.canonical_start, match.canonical_end)
        return Finding(
            detector=self._detector_id,
            detector_version=ruleset_version(),
            category=RULESET[match.pattern_name].category,
            status=FindingStatus.EXECUTED,
            confidence=_EXECUTED_CONFIDENCE,
            spans=(original_span,),
            evidence=None,
        )

    def detect(self, text: str, context: DetectorContext) -> tuple[Finding, ...]:
        """Canonicalize once, scan once, emit this family's findings (R1.2).

        ``context.budget`` is a guard REMAINING-TIME ``Budget`` (C30), NOT a
        Decode_Budget; it is not used to bound the decode (see the module
        docstring). The decode is bounded by the declared ``DEFAULT_BUDGET``.
        ``text`` is never mutated (R1.4). On a Decode_Budget rejection the
        detector cannot execute and emits one UNAVAILABLE Finding (R1.6 /
        R2.5), never an empty tuple presented as clean.
        """
        try:
            canonical, span_map = canonicalize(text, DEFAULT_BUDGET)
        except BudgetExceeded:
            return (self._unavailable(),)
        return tuple(
            self._finding(match, span_map)
            for match in scan(canonical)
            if self._owns(match)
        )


@dataclass(frozen=True, slots=True)
class PiiDetector(_DeterministicBase):
    """Deterministic PII detector (email / phone / SSN-like) -> ``Category.PII``."""

    _family: ClassVar[str] = "pii"
    _detector_id: ClassVar[str] = "deterministic.pii"
    _unavailable_category: ClassVar[Category] = Category.PII


@dataclass(frozen=True, slots=True)
class SecretDetector(_DeterministicBase):
    """Deterministic secret/credential detector -> ``Category.SECRET`` / ``CREDENTIAL``.

    Owns the ``secret`` family: AWS keys and generic API keys/tokens
    (``Category.SECRET``) plus PEM private-key headers and card numbers
    (``Category.CREDENTIAL``). The per-match category comes from the firing
    ``PatternSpec``.
    """

    _family: ClassVar[str] = "secret"
    _detector_id: ClassVar[str] = "deterministic.secret"
    _unavailable_category: ClassVar[Category] = Category.SECRET


@dataclass(frozen=True, slots=True)
class InjectionSignalDetector(_DeterministicBase):
    """Deterministic injection/command SIGNAL detector (R6, the §10.2.2 change).

    Owns the ``injection`` family and emits ``PROMPT_INJECTION`` / ``JAILBREAK``
    / ``COMMAND`` findings as a SIGNAL only -- a Finding, never a terminal block
    (a detector cannot decide disposition). Because the ruleset carries NO bare
    backtick-pair command pattern, a benign inline `` `code` `` prompt produces
    no finding (R6.3).
    """

    _family: ClassVar[str] = "injection"
    _detector_id: ClassVar[str] = "deterministic.injection"
    _unavailable_category: ClassVar[Category] = Category.PROMPT_INJECTION

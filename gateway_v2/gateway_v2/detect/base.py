"""The Detector contract (GW08, §10.5.1 / §10.4 step ④).

Step ④ of the lifecycle runs the plan-selected input detectors: deterministic
detectors always and in-process, a semantic detector only if the plan selects one;
each returns ``Finding[]`` with status ``EXECUTED | SKIPPED | UNAVAILABLE``. The
pure resolver at ⑤ consumes those findings — ``detect`` never decides disposition.

This module is the uniform :class:`Detector` protocol and the plan-derived
:class:`DetectorContext` it receives. ``Finding``, ``FindingStatus`` and ``Span``
are re-exported from :mod:`gateway_v2.domain.finding` (built on, never redefined).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.detect.guard.backend import Budget
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.plan import Phase, Rule


@dataclass(frozen=True, slots=True)
class DetectorContext:
    """The plan-derived context a detector receives.

    Carries WHAT the detector is selected for and HOW long it may take — never
    which backend is active.
    """

    phase: Phase  # input | output
    rules: tuple[Rule, ...]  # the rules this detector is selected for
    budget: Budget  # remaining budget for this invocation (C30)


@runtime_checkable
class Detector(Protocol):
    """A uniform, replaceable detector that is incapable of deciding anything.

    Hard rules: it emits ``Finding[]`` only; it MUST NOT mutate ``text``; it MUST
    NOT decide disposition; it MUST NOT raise HTTP; it MUST NOT import a higher
    layer (``edge`` / ``admit`` / ``plan``). A detector that cannot execute returns
    a Finding with ``status=FindingStatus.UNAVAILABLE`` — never an empty tuple
    presented as clean and never a benign ``EXECUTED`` finding.
    """

    detector_version: str  # the model_hash for a guard-backed detector
    required_capabilities: frozenset[str]

    def detect(self, text: str, context: DetectorContext) -> tuple[Finding, ...]: ...


__all__: tuple[str, ...] = (
    "Detector",
    "DetectorContext",
    "Finding",
    "FindingStatus",
    "Span",
)

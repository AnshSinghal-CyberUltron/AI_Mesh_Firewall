"""Detectors emit findings only. Never HTTP. Never resolve.

The GW08 public surface: the :class:`Detector` contract and its plan-derived
:class:`DetectorContext` (from :mod:`gateway_v2.detect.base`), the shipped
``Finding``/``FindingStatus``/``Span`` value types re-exported through ``base``, and
the registry's :func:`resolve_required_detectors` / :func:`select_backend` with the
:class:`UnknownDetectorError` / :class:`UnknownBackendError` load-failure types (from
:mod:`gateway_v2.detect.registry`). The GuardBackend surface lives under
:mod:`gateway_v2.detect.guard`.
"""

from __future__ import annotations

from gateway_v2.detect.base import (
    Detector,
    DetectorContext,
    Finding,
    FindingStatus,
    Span,
)
from gateway_v2.detect.registry import (
    UnknownBackendError,
    UnknownDetectorError,
    resolve_required_detectors,
    select_backend,
)

__all__: tuple[str, ...] = (
    "Detector",
    "DetectorContext",
    "Finding",
    "FindingStatus",
    "Span",
    "resolve_required_detectors",
    "select_backend",
    "UnknownDetectorError",
    "UnknownBackendError",
)

"""The detector registry and config-only backend selection (GW08, §10.4 ⑤ / §10.9).

Two responsibilities, both run ONCE at snapshot load — never per request:

1. :func:`resolve_required_detectors` resolves every id in
   ``ExecutionPlan.required_detectors`` to a :class:`~gateway_v2.detect.base.Detector`
   instance. Resolution is TOTAL-OR-FAIL: all ids resolve to exactly one detector each,
   or the whole load raises :class:`UnknownDetectorError` naming the unresolved id — the
   registry never silently drops an id and never resolves per request (R4.1/R4.2/R4.3).

2. :func:`select_backend` maps the ``AMF_GUARD_BACKEND`` deployment config value to a
   :class:`~gateway_v2.detect.guard.backend.GuardBackend` factory (Decision D). Selection
   is config-only: no source edit swaps a backend, and a swap selects a different backend
   whose loaded bytes — hence ``model_hash`` — differ, so ``Finding.detector_version`` and
   the audit row change accordingly (R3.5/R3.6). Unset config → ``local_onnx``, the CI
   default (R3.2).

House idiom: the two registration maps are immutable (``MappingProxyType``; the
holdback ``TRADE_OFF`` idiom), there is no module-level mutable binding, no capacity
literal, and no HTTP construct. ``detect`` imports no HIGHER layer — only sibling
``detect`` modules here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from gateway_v2.detect.base import Detector
from gateway_v2.detect.guard.backend import GuardBackend
from gateway_v2.detect.guard.local_onnx import build_local_onnx
from gateway_v2.detect.guard.local_trt import LocalTrtBackend
from gateway_v2.detect.guard.remote_http import RemoteHttpBackend
from gateway_v2.detect.guard.triton_grpc import TritonGrpcBackend

_DEFAULT_BACKEND = "local_onnx"


class UnknownDetectorError(KeyError):
    """A ``required_detectors`` id with no registration.

    Raised at snapshot load — never per request — and names the unresolved id so the
    load failure is self-describing (R4.3).
    """


class UnknownBackendError(KeyError):
    """An ``AMF_GUARD_BACKEND`` value naming no registered backend slot.

    Raised by :func:`select_backend` so a mis-configured deployment fails loudly with
    the offending name rather than silently falling back to a different backend.
    """


# Detector registration map: detector id → a factory that builds a Detector from the
# selected GuardBackend. Immutable (R7.5). The real detectors are GW09 (deterministic)
# and GW10 (semantic), which register their ids here later; this framework ships the
# resolution + fail-closed behaviour complete, so the map starts empty. Tests exercise
# the resolution by passing their own registry via the injectable ``registry`` param
# of :func:`resolve_required_detectors` (there is no module-level ``register`` mutation
# — the map is static, satisfying the no-module-mutable discipline).
_DETECTOR_REGISTRY: Mapping[str, Callable[[GuardBackend], Detector]] = MappingProxyType(
    {}
)


# Backend selection map: AMF_GUARD_BACKEND value → a factory taking the deployment
# config and returning a GuardBackend. The seam modules expose a no-arg ``make_backend``,
# so they are wrapped to the uniform ``(config) -> GuardBackend`` shape here; the working
# ``local_onnx`` factory already takes the config. Immutable (R7.5).
_BACKEND_FACTORIES: Mapping[str, Callable[[Mapping[str, str]], GuardBackend]] = (
    MappingProxyType(
        {
            "local_onnx": build_local_onnx,
            "local_trt": lambda config: LocalTrtBackend(),
            "triton_grpc": lambda config: TritonGrpcBackend(),
            "remote_http": lambda config: RemoteHttpBackend(),
        }
    )
)


def resolve_required_detectors(
    required: frozenset[str],
    *,
    backend: GuardBackend,
    registry: Mapping[str, Callable[[GuardBackend], Detector]] = _DETECTOR_REGISTRY,
) -> tuple[Detector, ...]:
    """Resolve every required-detector id to an instance at SNAPSHOT LOAD.

    Iterates ``sorted(required)`` for a deterministic order and builds one detector per
    id via its factory, closing each over ``backend``. An id absent from ``registry``
    raises :class:`UnknownDetectorError` naming it; resolution is total — all ids resolve
    or the whole call raises, with no partial result and no silent drop (R4.1/R4.3). This
    runs ONCE per snapshot; the returned tuple is reused per request (R4.2).

    ``registry`` is injectable (defaulting to the static module map) so tests supply their
    own id → factory map without any module-level mutation.
    """
    resolved: list[Detector] = []
    for det_id in sorted(required):
        factory = registry.get(det_id)
        if factory is None:
            raise UnknownDetectorError(det_id)
        resolved.append(factory(backend))
    return tuple(resolved)


def select_backend(config: Mapping[str, str]) -> GuardBackend:
    """Select the GuardBackend from deployment config (Decision D) — config-only.

    Reads ``AMF_GUARD_BACKEND`` (unset → ``local_onnx``, the CI default) and calls the
    matching factory with ``config``. An unrecognized name raises
    :class:`UnknownBackendError`. No source edit swaps a backend; a config swap selects a
    different backend whose loaded bytes — hence ``model_hash`` — differ (R3.5/R3.6).
    """
    name = config.get("AMF_GUARD_BACKEND", _DEFAULT_BACKEND)
    factory = _BACKEND_FACTORIES.get(name)
    if factory is None:
        raise UnknownBackendError(name)
    return factory(config)


__all__: tuple[str, ...] = (
    "UnknownDetectorError",
    "UnknownBackendError",
    "resolve_required_detectors",
    "select_backend",
)

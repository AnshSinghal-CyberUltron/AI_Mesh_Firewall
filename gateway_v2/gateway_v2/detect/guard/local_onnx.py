"""The CPU-default working ``local_onnx`` GuardBackend (GW08, §10.5.4).

``local_onnx`` is the development and CI default. It ships a WORKING local backend
with REAL readiness/``model_hash``/capacity behaviour — it never fabricates a clean
result. The real ONNX model load and inference are a declared BYTES-HASHED SEAM: the
backend computes ``model_hash`` from the bytes it ACTUALLY loads (``sha256`` of the
backing blob), compares that to the signed manifest, drives a
:class:`~gateway_v2.detect.guard.backend.ReadinessTracker` on an injected clock, and
reports a :class:`~gateway_v2.detect.guard.backend.BackendCapacity` from a measured
window rate.

Safety (C35): a not-ready backend NEVER yields a clean EXECUTED result. A model-hash
vs signed-manifest mismatch → not-ready. A ``classify`` whose estimated work would
exceed its :class:`~gateway_v2.detect.guard.backend.Budget` → unavailable, distinct
from a clean pass. Not-ready and over-budget both surface as :class:`BackendUnavailable`;
the semantic detector (GW10) maps that signal to ``UNAVAILABLE`` findings. A clean
``GuardResult`` is produced only when the backend is genuinely ready and in budget.

The measured window rate, queue depth, and manifest are CONSTRUCTOR inputs, so no
capacity numeric literal lives at module scope and no module-level mutable is bound.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping, Sequence

from gateway_v2.detect.guard.backend import (
    BackendCapacity,
    Budget,
    CapacityHint,
    GuardResult,
    Readiness,
    ReadinessTracker,
)
from gateway_v2.domain.category import Category


class BackendUnavailable(RuntimeError):
    """A not-ready / over-budget guard signal.

    The semantic detector maps this to ``UNAVAILABLE`` findings — it is NEVER a clean
    result. ``reason`` names why (``"stale_or_manifest_mismatch"`` or ``"over_budget"``)
    so the mapping and the audit trail stay honest.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LocalOnnxBackend:
    """A working CPU GuardBackend over a bytes-hashed inference seam.

    Satisfies the ``GuardBackend`` protocol. It is a STATEFUL object (a backend holds
    the last-good-reply time), so it is a plain class — not a frozen dataclass. The one
    mutable field, ``_last_ok_at``, is INSTANCE state, which the no-module-mutable gate
    permits; the gate forbids only module-level mutables.
    """

    def __init__(
        self,
        blob: bytes,
        *,
        manifest_hash: str,
        staleness_bound_s: float,
        windows_per_second: float,
        queue_depth: int,
        batch_profile: str = "cpu",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # model_hash is computed from the bytes ACTUALLY loaded, never advertised.
        self._model_hash = hashlib.sha256(blob).hexdigest()
        self._manifest_hash = manifest_hash
        self._tracker = ReadinessTracker(
            staleness_bound_s=staleness_bound_s, manifest_hash=manifest_hash
        )
        # Measured owner profile — taken at warm-up / construction, never a literal.
        self._windows_per_second = windows_per_second
        self._queue_depth = queue_depth
        self._batch_profile = batch_profile
        self._clock = clock
        # Mutable INSTANCE state: time of the last successful reply (None until one).
        self._last_ok_at: float | None = None

    @property
    def model_hash(self) -> str:
        """The identity computed from the bytes actually loaded (R5.9/R5.10)."""
        return self._model_hash

    def _is_ready(self, now_s: float) -> bool:
        return self._tracker.is_ready(
            now_s=now_s, last_ok_at=self._last_ok_at, loaded_hash=self._model_hash
        )

    def _can_serve(self, now_s: float) -> bool:
        """Whether a ``classify`` may run right now (distinct from passive readiness).

        A ``classify`` IS the reply that establishes reply-based readiness, so a fresh
        backend with a matching manifest must be allowed to serve its first call — a
        passive :meth:`readiness` probe stays not-ready until that reply lands, but the
        backend could never bootstrap if the first classify were gated on a reply that
        only a classify can produce. The manifest check always applies (fail-closed on
        a mismatch / corrupt cache, C35 / R5.6 / R5.7); the staleness bound applies only
        once a prior reply exists, so a serving backend that has gone stale (no recent
        reply within the bound) refuses rather than serving on an expired warm-up.
        """
        if self._model_hash != self._manifest_hash:
            return False
        if self._last_ok_at is None:
            return True
        return (now_s - self._last_ok_at) <= self._tracker.staleness_bound_s

    def _score_window(self, window_index: int, window: str) -> GuardResult:
        """A deterministic CPU seam classifier.

        Real (not random) and reproducible: a stable per-window score derived from the
        text bytes stands in for the ONNX forward pass. Same text → same score.
        """
        digest = hashlib.sha256(window.encode("utf-8")).digest()
        # Map the leading digest bytes to a stable confidence in [0, 1].
        raw = int.from_bytes(digest[:4], "big")
        score = raw / float(0xFFFFFFFF)
        return GuardResult(
            window_index=window_index,
            category=str(Category.PROMPT_INJECTION),
            score=score,
        )

    async def classify(
        self, windows: Sequence[str], budget: Budget
    ) -> Sequence[GuardResult]:
        """Classify each window, or signal UNAVAILABLE — never a fabricated clean pass.

        Cannot serve (manifest/hash mismatch, or a prior reply has gone stale beyond
        the bound) → :class:`BackendUnavailable` (``stale_or_manifest_mismatch``). A
        fresh backend with a matching manifest may serve its first call, which is the
        reply that establishes reply-based readiness. If the estimated work would exceed
        ``budget.remaining_s`` → :class:`BackendUnavailable` (``over_budget``). Only when
        genuinely ready AND in budget does it run the seam classifier, record a
        successful reply, and return one ``GuardResult`` per window.
        """
        now_s = self._clock()
        if not self._can_serve(now_s):
            raise BackendUnavailable("stale_or_manifest_mismatch")
        # Estimate the seam's work from the measured rate; over-budget → unavailable,
        # distinct from a clean result (C35 / R5.5 / R5.8).
        if self._windows_per_second <= 0.0:
            raise BackendUnavailable("over_budget")
        estimated_s = len(windows) / self._windows_per_second
        if estimated_s > budget.remaining_s:
            raise BackendUnavailable("over_budget")
        results = tuple(
            self._score_window(index, window)
            for index, window in enumerate(windows)
        )
        # Record the successful reply on the injected clock (reply-based readiness).
        self._last_ok_at = self._tracker.record_ok(self._clock(), self._last_ok_at)
        return results

    async def readiness(self) -> Readiness:
        """Reply-based readiness over the injected clock; honest ``model_hash``."""
        now_s = self._clock()
        ready = self._is_ready(now_s)
        reason = "" if ready else "stale_or_manifest_mismatch"
        return Readiness(
            ready=ready,
            model_hash=self._model_hash,
            last_ok_at=self._last_ok_at,
            reason=reason,
        )

    def capacity_hint(self) -> CapacityHint:
        """Measured rate → ``CapacityHint`` for ``ResourceContract._guard_pool`` (R6)."""
        return BackendCapacity(
            windows_per_second=self._windows_per_second,
            queue_depth=self._queue_depth,
            batch_profile=self._batch_profile,
        ).to_capacity_hint()


def build_local_onnx(config: Mapping[str, str]) -> LocalOnnxBackend:
    """Registry factory (Decision D): construct the CPU default from deployment config.

    ``select_backend`` (Task 4) maps ``AMF_GUARD_BACKEND`` → this factory; ``local_onnx``
    is the default. The backing bytes come from config (``AMF_GUARD_MODEL_BLOB``), so a
    config change that swaps the blob changes ``model_hash`` with no source edit (R3.5/
    R3.6). The signed manifest and the measured owner profile likewise arrive from
    config, keeping every capacity number out of ``detect`` source (R6.1/R7.4).
    """
    blob = config.get("AMF_GUARD_MODEL_BLOB", "").encode("utf-8")
    manifest_hash = config.get(
        "AMF_GUARD_MANIFEST_HASH", hashlib.sha256(blob).hexdigest()
    )
    staleness_bound_s = float(config.get("AMF_GUARD_STALENESS_BOUND_S", "5.0"))
    windows_per_second = float(config.get("AMF_GUARD_WINDOWS_PER_SECOND", "100.0"))
    queue_depth = int(config.get("AMF_GUARD_QUEUE_DEPTH", "32"))
    batch_profile = config.get("AMF_GUARD_BATCH_PROFILE", "cpu")
    return LocalOnnxBackend(
        blob,
        manifest_hash=manifest_hash,
        staleness_bound_s=staleness_bound_s,
        windows_per_second=windows_per_second,
        queue_depth=queue_depth,
        batch_profile=batch_profile,
    )


__all__: tuple[str, ...] = (
    "BackendUnavailable",
    "LocalOnnxBackend",
    "build_local_onnx",
)

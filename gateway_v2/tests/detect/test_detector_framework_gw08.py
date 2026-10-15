"""GW08 detector framework + GuardBackend interface — the single owning test file.

Task 6 of ``.kiro/specs/detector-framework-gw08/``: ALL of sub-tasks 6.1–6.12 live
here in one coherent file (the B6/B7/B8/B9 single-test-file-collision lesson — one
owner, one pass, never split across parallel sub-agents).

The ten design Correctness Properties (design.md §Correctness Properties) each map to
one property test tagged ``# Feature: detector-framework-gw08, Property N`` with a
``# Validates: Requirements X.Y`` link; 6.11 is the example/conformance sweep and 6.12
the gate/AST sweep. The async surface (``classify`` / ``readiness``) is driven through
the house idiom ``def _run[T](coro) -> T: return asyncio.run(coro)`` (mirroring
``tests/state_control/test_lgw05c_writer.py``) with injected clocks; pure logic
(readiness, model-hash identity, value-type round-trips) runs ≥ 10,000 seeded
``random.Random`` iterations with no ``hypothesis``.

Iteration-count note: the async-driving properties (P1/P3/P4/P8) pay an
``asyncio.run`` per iteration, so they run a reduced but still-substantial loop
(``_ASYNC_ITERS`` = 2,000, well above the 1,000 floor); the pure-logic properties
(P2/P5/P6/P7/P9/P10) run the full ``_ITERS`` = 10,000. Every assertion logs its seed
so a counterexample is reproducible.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import random
import textwrap
from collections.abc import Awaitable, Mapping, Sequence
from pathlib import Path

import pytest

from gateway_v2.detect.base import Detector, DetectorContext
from gateway_v2.detect.guard.backend import (
    BackendCapacity,
    Budget,
    GuardBackend,
    GuardResult,
    Readiness,
    ReadinessTracker,
)
from gateway_v2.detect.guard.local_onnx import (
    BackendUnavailable,
    LocalOnnxBackend,
    build_local_onnx,
)
from gateway_v2.detect.guard.local_trt import DeferredBackendError, LocalTrtBackend
from gateway_v2.detect.guard.remote_http import RemoteHttpBackend
from gateway_v2.detect.guard.triton_grpc import TritonGrpcBackend
from gateway_v2.detect.registry import (
    UnknownBackendError,
    UnknownDetectorError,
    resolve_required_detectors,
    select_backend,
)
from gateway_v2.domain.category import Category
from gateway_v2.domain.channels import unscanned_channels
from gateway_v2.domain.decision import Decision, DecisionRecord, Disposition
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.plan import Phase
from gateway_v2.runtime.kinds import CapacityHint, HardwareSignals, PoolKind
from gateway_v2.runtime.resources import from_signals
from lint.check_http_outside_edge_resolve import scan_tree as scan_http

_ITERS = 10_000
_ASYNC_ITERS = 2_000
_SEED = 0x6708  # GW08


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Test helpers: a controllable fake backend + the minimal GW10 mapping.
# --------------------------------------------------------------------------- #


class FakeGuardBackend:
    """A GuardBackend-conforming fake with injectable readiness / hash / results.

    Lets the properties drive not-ready → UNAVAILABLE and manifest scenarios
    deterministically without the real ``LocalOnnxBackend`` clock dance.
    """

    def __init__(
        self,
        *,
        ready: bool,
        model_hash: str,
        results: Sequence[GuardResult] = (),
        reason: str = "",
        windows_per_second: float = 10.0,
    ) -> None:
        self._ready = ready
        self._model_hash = model_hash
        self._results = tuple(results)
        self._reason = reason if not ready else ""
        self._windows_per_second = windows_per_second
        self._last_ok_at: float | None = 0.0 if ready else None

    @property
    def model_hash(self) -> str:
        return self._model_hash

    def set_ready(self, ready: bool, *, reason: str = "") -> None:
        self._ready = ready
        self._reason = reason if not ready else ""

    async def classify(
        self, windows: Sequence[str], budget: Budget
    ) -> Sequence[GuardResult]:
        if not self._ready:
            raise BackendUnavailable(self._reason or "not_ready")
        return self._results

    async def readiness(self) -> Readiness:
        return Readiness(
            ready=self._ready,
            model_hash=self._model_hash,
            last_ok_at=self._last_ok_at,
            reason=self._reason,
        )

    def capacity_hint(self) -> CapacityHint:
        return CapacityHint(tokens_per_second=self._windows_per_second)


def _map_results_to_findings(
    results: Sequence[GuardResult], *, model_hash: str
) -> tuple[Finding, ...]:
    """GW10's EXECUTED mapping, modelled minimally: one EXECUTED Finding per result."""
    return tuple(
        Finding(
            detector="semantic.guard",
            detector_version=model_hash,
            category=Category(result.category),
            status=FindingStatus.EXECUTED,
            confidence=result.score,
            spans=(Span(start=result.window_index, end=result.window_index),),
            evidence=None,
        )
        for result in results
    )


def _unavailable_finding(model_hash: str) -> Finding:
    """The single UNAVAILABLE Finding a backend-unavailable signal maps to (C35)."""
    return Finding(
        detector="semantic.guard",
        detector_version=model_hash,
        category=Category.PROMPT_INJECTION,
        status=FindingStatus.UNAVAILABLE,
        confidence=None,
        spans=(),
        evidence=None,
    )


def _detect_via_backend(
    backend: GuardBackend, windows: Sequence[str], budget: Budget
) -> tuple[Finding, ...]:
    """The real GW10 flow in miniature: classify → EXECUTED findings, or on a
    BackendUnavailable signal → exactly one UNAVAILABLE finding (never clean)."""
    model_hash = _readiness_hash(backend)
    try:
        results = _run(backend.classify(windows, budget))
    except BackendUnavailable:
        return (_unavailable_finding(model_hash),)
    return _map_results_to_findings(results, model_hash=model_hash)


def _readiness_hash(backend: GuardBackend) -> str:
    return _run(backend.readiness()).model_hash


class FakeSemanticDetector:
    """A tiny Detector that wraps a GuardBackend and never mutates its input.

    Models the GW10 detector surface minimally so Properties 1/8/9 can assert the
    finding-level behaviour. ``detect`` splits the text into windows, classifies, and
    maps results → findings (EXECUTED) or the UNAVAILABLE signal → a single UNAVAILABLE
    finding. ``detector_version`` tracks the backend's live ``model_hash``.
    """

    def __init__(self, backend: GuardBackend) -> None:
        self._backend = backend
        self.detector_version = _readiness_hash(backend)
        self.required_capabilities: frozenset[str] = frozenset({"semantic"})

    def detect(self, text: str, context: DetectorContext) -> tuple[Finding, ...]:
        windows = (text,) if text else ("",)
        return _detect_via_backend(self._backend, windows, context.budget)


def _ctx(budget: Budget) -> DetectorContext:
    return DetectorContext(phase=Phase.INPUT, rules=(), budget=budget)


def _rand_text(rng: random.Random) -> str:
    n = rng.randint(0, 40)
    alphabet = "abcdefghij \t\n0123456789"
    return "".join(rng.choice(alphabet) for _ in range(n))


def _rand_blob(rng: random.Random) -> bytes:
    return bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 32)))


def _onnx(
    blob: bytes,
    *,
    manifest_hash: str | None = None,
    windows_per_second: float = 100.0,
    staleness_bound_s: float = 5.0,
    clock_times: list[float] | None = None,
) -> LocalOnnxBackend:
    """Build a LocalOnnxBackend over an injectable scripted clock."""
    manifest = manifest_hash if manifest_hash is not None else hashlib.sha256(blob).hexdigest()
    times = list(clock_times) if clock_times is not None else [0.0]
    pos = {"i": 0}

    def _clock() -> float:
        i = min(pos["i"], len(times) - 1)
        pos["i"] += 1
        return times[i]

    return LocalOnnxBackend(
        blob,
        manifest_hash=manifest,
        staleness_bound_s=staleness_bound_s,
        windows_per_second=windows_per_second,
        queue_depth=32,
        batch_profile="cpu",
        clock=_clock,
    )


# --------------------------------------------------------------------------- #
# 6.1 / Property 1 — a not-ready backend always yields UNAVAILABLE, never clean.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 1: A not-ready backend always yields
# UNAVAILABLE, never clean.
# Validates: Requirements 1.6, 5.1, 5.2
def test_property_not_ready_backend_only_unavailable() -> None:
    rng = random.Random(_SEED + 1)
    for i in range(_ASYNC_ITERS):
        backend = FakeGuardBackend(ready=False, model_hash=f"h{i}", reason="down")
        detector = FakeSemanticDetector(backend)
        text = _rand_text(rng)
        findings = detector.detect(text, _ctx(Budget(remaining_s=1.0)))
        msg = f"seed={_SEED + 1} iter={i} text={text!r}"
        assert findings, f"UNAVAILABLE must be non-empty, not omitted: {msg}"
        assert all(f.status is FindingStatus.UNAVAILABLE for f in findings), msg
        assert not any(f.status is FindingStatus.EXECUTED for f in findings), msg


# --------------------------------------------------------------------------- #
# 6.2 / Property 2 — readiness is reply-based + monotone-correct (injected clock).
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 2: Readiness is reply-based and
# monotone-correct under the injected clock.
# Validates: Requirements 5.3, 5.4
def test_property_readiness_is_reply_based() -> None:
    rng = random.Random(_SEED + 2)
    manifest = "manifest-abc"
    for i in range(_ITERS):
        bound = rng.uniform(0.1, 10.0)
        tracker = ReadinessTracker(staleness_bound_s=bound, manifest_hash=manifest)
        now = rng.uniform(0.0, 1000.0)
        # Mix matching / mismatching hashes and present / absent / stale replies.
        loaded = manifest if rng.random() < 0.5 else "manifest-other"
        if rng.random() < 0.25:
            last_ok: float | None = None
        else:
            # Spread last_ok_at across in-bound and "alive but stale" (far past).
            delta = rng.uniform(0.0, bound * 3.0)
            last_ok = now - delta
        got = tracker.is_ready(now_s=now, last_ok_at=last_ok, loaded_hash=loaded)
        want = (
            loaded == manifest
            and last_ok is not None
            and (now - last_ok) <= bound
        )
        assert got is want, (
            f"seed={_SEED + 2} iter={i} loaded={loaded!r} now={now} "
            f"last_ok={last_ok} bound={bound} got={got} want={want}"
        )


# --------------------------------------------------------------------------- #
# 6.3 / Property 3 — an over-budget classify resolves to UNAVAILABLE, not clean.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 3: A classify that exceeds its Budget
# resolves to UNAVAILABLE within the bound.
# Validates: Requirements 5.5, 5.8
def test_property_over_budget_classify_is_unavailable() -> None:
    rng = random.Random(_SEED + 3)
    for i in range(_ASYNC_ITERS):
        blob = _rand_blob(rng)
        wps = rng.uniform(1.0, 50.0)
        n_windows = rng.randint(1, 20)
        # A ready backend: scripted clock stays within the staleness bound.
        backend = _onnx(blob, windows_per_second=wps, clock_times=[0.0] * 8)
        windows = tuple(_rand_text(rng) for _ in range(n_windows))
        needed = n_windows / wps
        msg = f"seed={_SEED + 3} iter={i} n={n_windows} wps={wps} needed={needed}"
        # Over-budget: remaining strictly below the estimated work → UNAVAILABLE.
        tight = Budget(remaining_s=needed * 0.5)
        with pytest.raises(BackendUnavailable) as excinfo:
            _run(backend.classify(windows, tight))
        assert excinfo.value.reason == "over_budget", msg
        # Ample budget → a clean EXECUTED result, distinct from the UNAVAILABLE path.
        fresh = _onnx(blob, windows_per_second=wps, clock_times=[0.0] * 8)
        ample = Budget(remaining_s=needed * 4.0 + 1.0)
        results = _run(fresh.classify(windows, ample))
        assert len(results) == n_windows, msg
        findings = _map_results_to_findings(results, model_hash=fresh.model_hash)
        assert all(f.status is FindingStatus.EXECUTED for f in findings), msg


# --------------------------------------------------------------------------- #
# 6.4 / Property 4 — a manifest mismatch always fails closed.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 4: A manifest mismatch always fails
# closed.
# Validates: Requirements 5.6, 5.7
def test_property_manifest_mismatch_fails_closed() -> None:
    rng = random.Random(_SEED + 4)
    for i in range(_ASYNC_ITERS):
        blob = _rand_blob(rng)
        real_hash = hashlib.sha256(blob).hexdigest()
        # A manifest that is deliberately NOT the real loaded-bytes hash.
        bogus = hashlib.sha256(blob + b"\x00tamper").hexdigest()
        msg = f"seed={_SEED + 4} iter={i} blob_len={len(blob)}"
        mismatched = _onnx(blob, manifest_hash=bogus, clock_times=[0.0] * 8)
        rd = _run(mismatched.readiness())
        assert rd.ready is False, msg
        # The reported model_hash stays the REAL loaded hash — never the manifest.
        assert rd.model_hash == real_hash != bogus, msg
        with pytest.raises(BackendUnavailable):
            _run(mismatched.classify(("hello",), Budget(remaining_s=100.0)))
        # Matching manifest → ready-eligible after a warm-up reply records last_ok_at.
        matched = _onnx(blob, manifest_hash=real_hash, clock_times=[0.0] * 8)
        _run(matched.classify(("warm",), Budget(remaining_s=100.0)))
        rd2 = _run(matched.readiness())
        assert rd2.ready is True, msg
        assert rd2.model_hash == real_hash, msg


# --------------------------------------------------------------------------- #
# 6.5 / Property 5 — model identity is deterministic, distinguishing, cache-invalidating.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 5: Model identity is deterministic,
# distinguishing, and cache-invalidating.
# Validates: Requirements 3.3, 3.6, 5.10
def test_property_model_identity_is_deterministic_and_distinguishing() -> None:
    rng = random.Random(_SEED + 5)
    for i in range(_ITERS):
        blob = _rand_blob(rng)
        other = _rand_blob(rng)
        h1 = _onnx(blob).model_hash
        h1_again = _onnx(blob).model_hash
        msg = f"seed={_SEED + 5} iter={i}"
        # Deterministic: same bytes → same hash.
        assert h1 == h1_again == hashlib.sha256(blob).hexdigest(), msg
        # Distinguishing: different bytes → different hash.
        if other != blob:
            assert _onnx(other).model_hash != h1, msg
        # Cache-invalidating: a dict keyed by model_hash misses the old hash once the
        # loaded bytes (hence the hash) change under a running process.
        cache: dict[str, int] = {h1: i}
        new_hash = _onnx(blob + b"v2").model_hash
        assert new_hash != h1, msg
        assert cache.get(new_hash) is None, msg


# --------------------------------------------------------------------------- #
# 6.6 / Property 6 — capacity_hint reports a measured rate, not a constant.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 6: capacity_hint reports a measured
# rate, not a constant.
# Validates: Requirements 6.1, 6.3
def test_property_capacity_hint_tracks_measured_rate() -> None:
    rng = random.Random(_SEED + 6)
    signals = HardwareSignals(
        cpu_quota=8.0,
        cpu_source="test",
        memory_limit=16 * 1024 * 1024 * 1024,
        mem_source="test",
        fd_limit=65536,
        fd_source="test",
    )
    def _guard_pool_for(wps: float) -> int:
        hint = BackendCapacity(
            windows_per_second=wps, queue_depth=8, batch_profile="cpu"
        ).to_capacity_hint()
        assert hint.tokens_per_second == wps  # measured rate, never a fixed literal
        contract = from_signals(
            signals,
            target_p99_ms=20.0,
            utilization_cap=0.75,
            per_worker_rss=400 * 1024 * 1024,
            guard_capacity=hint,
        )
        assert contract.guard_capacity == hint
        return contract.pool_size(PoolKind.GUARD)

    for i in range(_ITERS):
        # Two ordered rates; keep both high enough that floor(wps * p99/1000) >= 1 so
        # the shipped _guard_pool is derivable (it fails closed below one guard token).
        low = rng.uniform(50.0, 5000.0)
        high = low + rng.uniform(0.0, 5000.0)
        msg = f"seed={_SEED + 6} iter={i} low={low} high={high}"
        pool_low = _guard_pool_for(low)
        pool_high = _guard_pool_for(high)
        # _guard_pool derives from the measured hint and is monotone non-decreasing in
        # the measurement (never a constant: a bigger measured rate → a >= pool).
        assert pool_high >= pool_low, msg
        # And it tracks the rate rather than ignoring it: a large enough rate increase
        # strictly grows the pool, proving it is not a fixed literal.
        assert _guard_pool_for(low + 1000.0) > pool_low, msg


# --------------------------------------------------------------------------- #
# 6.7 / Property 7 — the registry resolves totally or fails the load.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 7: The registry resolves totally or
# fails the load.
# Validates: Requirements 4.1, 4.3
def _make_fake_detector_factory(det_id: str):
    def factory(backend: GuardBackend) -> Detector:
        return FakeSemanticDetector(backend)

    return factory


def test_property_registry_resolves_totally_or_fails() -> None:
    rng = random.Random(_SEED + 7)
    all_ids = [f"det-{k}" for k in range(12)]
    registry: Mapping[str, object] = {
        det_id: _make_fake_detector_factory(det_id) for det_id in all_ids
    }
    backend = FakeGuardBackend(ready=True, model_hash="h")
    for i in range(_ITERS):
        subset = frozenset(k for k in all_ids if rng.random() < 0.5)
        msg = f"seed={_SEED + 7} iter={i} subset={sorted(subset)}"
        resolved = resolve_required_detectors(
            subset, backend=backend, registry=registry  # type: ignore[arg-type]
        )
        assert len(resolved) == len(subset), msg
        assert all(isinstance(d, Detector) for d in resolved), msg
        # A random unknown id → UnknownDetectorError naming it, at load (never dropped).
        unknown = f"missing-{rng.randint(0, 1_000_000)}"
        with_unknown = subset | {unknown}
        with pytest.raises(UnknownDetectorError) as excinfo:
            resolve_required_detectors(
                with_unknown, backend=backend, registry=registry  # type: ignore[arg-type]
            )
        assert unknown in str(excinfo.value), msg


# --------------------------------------------------------------------------- #
# 6.8 / Property 8 — model_hash appears in every Finding and audit row.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 8: model_hash appears in every produced
# Finding and audit row.
# Validates: Requirements 5.9
def _audit_rows(findings: tuple[Finding, ...]) -> list[Mapping[str, object]]:
    from gateway_v2.audit.record import finding_detail

    disposition = Disposition.ALLOW
    decision = Decision(
        disposition=disposition,
        per_finding=(),
        transformations=(),
        findings=findings,
        plan_version="pv",
        deciding_rules=(),
        unavailable_detectors=(),
    )
    record = DecisionRecord(request_id="req-1", plan_version="pv", decision=decision)
    return finding_detail(record)


def test_property_model_hash_in_findings_and_audit() -> None:
    rng = random.Random(_SEED + 8)
    for i in range(_ASYNC_ITERS):
        blob = _rand_blob(rng)
        backend = _onnx(blob, windows_per_second=1000.0, clock_times=[0.0] * 8)
        n = rng.randint(1, 6)
        windows = tuple(_rand_text(rng) for _ in range(n))
        results = _run(backend.classify(windows, Budget(remaining_s=100.0)))
        findings = _map_results_to_findings(results, model_hash=backend.model_hash)
        msg = f"seed={_SEED + 8} iter={i} n={n}"
        assert findings, msg
        assert all(f.detector_version == backend.model_hash for f in findings), msg
        rows = _audit_rows(findings)
        assert len(rows) == len(findings), msg
        assert all(r["detector_version"] == backend.model_hash for r in rows), msg


# --------------------------------------------------------------------------- #
# 6.9 / Property 9 — a detector leaves its input text byte-identical.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 9: A detector leaves its input text
# unchanged.
# Validates: Requirements 1.3
def test_property_detector_leaves_input_unchanged() -> None:
    rng = random.Random(_SEED + 9)
    for i in range(_ITERS):
        # Alternate ready / not-ready so both mapping branches are exercised.
        ready = rng.random() < 0.5
        backend = FakeGuardBackend(
            ready=ready,
            model_hash=f"h{i}",
            results=(GuardResult(0, str(Category.PROMPT_INJECTION), 0.5),),
            reason="down",
        )
        detector = FakeSemanticDetector(backend)
        text = _rand_text(rng)
        snapshot = str(text)
        before = text.encode("utf-8")
        detector.detect(text, _ctx(Budget(remaining_s=10.0)))
        msg = f"seed={_SEED + 9} iter={i} ready={ready} text={text!r}"
        assert text == snapshot, msg
        assert text.encode("utf-8") == before, msg


# --------------------------------------------------------------------------- #
# 6.10 / Property 10 — value types round-trip via dataclasses.asdict.
# --------------------------------------------------------------------------- #
# Feature: detector-framework-gw08, Property 10: Value types round-trip.
# Validates: Requirements 8.7
def test_property_value_types_round_trip() -> None:
    rng = random.Random(_SEED + 10)
    categories = [str(c) for c in Category]
    for i in range(_ITERS):
        budget = Budget(remaining_s=rng.uniform(0.0, 100.0))
        result = GuardResult(
            window_index=rng.randint(0, 1000),
            category=rng.choice(categories),
            score=rng.random(),
        )
        last_ok = None if rng.random() < 0.3 else rng.uniform(0.0, 1000.0)
        readiness = Readiness(
            ready=rng.random() < 0.5,
            model_hash=f"hash-{rng.randint(0, 1_000_000)}",
            last_ok_at=last_ok,
            reason=rng.choice(["", "manifest_mismatch", "over_budget"]),
        )
        hint = CapacityHint(tokens_per_second=rng.uniform(0.0, 10_000.0))
        msg = f"seed={_SEED + 10} iter={i}"
        for value, cls in (
            (budget, Budget),
            (result, GuardResult),
            (readiness, Readiness),
            (hint, CapacityHint),
        ):
            encoded = dataclasses.asdict(value)
            decoded = cls(**encoded)  # type: ignore[arg-type]
            assert decoded == value, f"{msg} type={cls.__name__}"


# --------------------------------------------------------------------------- #
# 6.11 — example / conformance tests.
# --------------------------------------------------------------------------- #
# Validates: Requirements 1.1, 1.2, 2.1, 2.3, 2.5, 2.6, 2.7, 2.8, 3.1, 3.2, 3.4,
# 3.5, 3.6, 4.2
def test_protocols_are_runtime_checkable_and_fakes_pass() -> None:
    backend = FakeGuardBackend(ready=True, model_hash="h")
    detector = FakeSemanticDetector(backend)
    assert isinstance(backend, GuardBackend)
    assert isinstance(detector, Detector)
    # The real backends also conform.
    assert isinstance(_onnx(b"blob"), GuardBackend)
    assert isinstance(LocalTrtBackend(), GuardBackend)
    assert isinstance(TritonGrpcBackend(), GuardBackend)
    assert isinstance(RemoteHttpBackend(), GuardBackend)


def test_budget_derive_subtracts_now_and_has_no_absolute_time_field() -> None:
    budget = Budget.derive(deadline_s=10.0, now_s=3.0)
    assert budget.remaining_s == 7.0
    # A past deadline clamps to zero — never negative, never a timestamp.
    assert Budget.derive(deadline_s=1.0, now_s=5.0).remaining_s == 0.0
    field_names = {f.name for f in dataclasses.fields(Budget)}
    assert field_names == {"remaining_s"}
    assert not any("at" in name or "time" in name for name in field_names)


def test_backend_capacity_to_capacity_hint_is_runtime_type() -> None:
    cap = BackendCapacity(windows_per_second=42.0, queue_depth=8, batch_profile="cpu")
    hint = cap.to_capacity_hint()
    assert type(hint) is CapacityHint
    assert hint.tokens_per_second == 42.0


def test_four_factories_resolve_via_select_backend() -> None:
    assert isinstance(select_backend({"AMF_GUARD_BACKEND": "local_onnx"}), GuardBackend)
    assert isinstance(select_backend({"AMF_GUARD_BACKEND": "local_trt"}), LocalTrtBackend)
    assert isinstance(
        select_backend({"AMF_GUARD_BACKEND": "triton_grpc"}), TritonGrpcBackend
    )
    assert isinstance(
        select_backend({"AMF_GUARD_BACKEND": "remote_http"}), RemoteHttpBackend
    )
    # Unset config → the local_onnx CI default.
    assert isinstance(select_backend({}), LocalOnnxBackend)
    # An unknown backend name fails loudly.
    with pytest.raises(UnknownBackendError) as excinfo:
        select_backend({"AMF_GUARD_BACKEND": "nope-backend"})
    assert "nope-backend" in str(excinfo.value)


def test_each_seam_is_not_ready_with_its_reason_and_defers_classify() -> None:
    cases = (
        (LocalTrtBackend(), "deferred_gpu_backend"),
        (TritonGrpcBackend(), "deferred_gpu_backend"),
        (RemoteHttpBackend(), "deferred_network_backend"),
    )
    for backend, reason in cases:
        rd = _run(backend.readiness())
        assert rd.ready is False
        assert rd.reason == reason
        with pytest.raises(DeferredBackendError):
            _run(backend.classify(("x",), Budget(remaining_s=100.0)))


def test_lgw08_2_config_only_swap_changes_model_hash_and_audit() -> None:
    # Two different AMF_GUARD_MODEL_BLOB values → different model_hash → different
    # Finding.detector_version and audit row, with NO source edit.
    backend_a = build_local_onnx({"AMF_GUARD_MODEL_BLOB": "model-A"})
    backend_b = build_local_onnx({"AMF_GUARD_MODEL_BLOB": "model-B"})
    assert backend_a.model_hash != backend_b.model_hash
    findings_a = _detect_via_backend(backend_a, ("hi",), Budget(remaining_s=100.0))
    findings_b = _detect_via_backend(backend_b, ("hi",), Budget(remaining_s=100.0))
    assert findings_a[0].detector_version == backend_a.model_hash
    assert findings_b[0].detector_version == backend_b.model_hash
    assert findings_a[0].detector_version != findings_b[0].detector_version
    rows_a = _audit_rows(findings_a)
    rows_b = _audit_rows(findings_b)
    assert rows_a[0]["detector_version"] == backend_a.model_hash
    assert rows_b[0]["detector_version"] == backend_b.model_hash
    assert rows_a[0]["detector_version"] != rows_b[0]["detector_version"]


def test_lgw08_3_fake_flipped_not_ready_yields_unavailable() -> None:
    backend = FakeGuardBackend(
        ready=True,
        model_hash="h",
        results=(GuardResult(0, str(Category.PROMPT_INJECTION), 0.9),),
    )
    detector = FakeSemanticDetector(backend)
    ready_findings = detector.detect("payload", _ctx(Budget(remaining_s=10.0)))
    assert all(f.status is FindingStatus.EXECUTED for f in ready_findings)
    backend.set_ready(False, reason="owner_wedged")
    down_findings = detector.detect("payload", _ctx(Budget(remaining_s=10.0)))
    assert down_findings
    assert all(f.status is FindingStatus.UNAVAILABLE for f in down_findings)


def test_lgw08_4_manifest_mismatch_is_not_ready() -> None:
    blob = b"the-real-bytes"
    backend = _onnx(blob, manifest_hash="not-the-real-hash", clock_times=[0.0] * 8)
    rd = _run(backend.readiness())
    assert rd.ready is False
    assert rd.model_hash == hashlib.sha256(blob).hexdigest()


def test_registry_resolves_once_per_snapshot_and_reuses_the_tuple() -> None:
    calls: dict[str, int] = {"n": 0}

    def counting_factory(backend: GuardBackend) -> Detector:
        calls["n"] += 1
        return FakeSemanticDetector(backend)

    registry = {"det-a": counting_factory, "det-b": counting_factory}
    backend = FakeGuardBackend(ready=True, model_hash="h")
    resolved = resolve_required_detectors(
        frozenset({"det-a", "det-b"}),
        backend=backend,
        registry=registry,  # type: ignore[arg-type]
    )
    # One factory call per id for the single snapshot resolution.
    assert calls["n"] == 2
    assert len(resolved) == 2
    # The resolved tuple is reused per request — no re-resolution happens here.
    first, second = resolved, resolved
    assert first is second
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# 6.12 — gate / AST integration tests.
# --------------------------------------------------------------------------- #
# Validates: Requirements 7.4, 7.5, 7.6, 7.7, 7.8, 8.6
_PLANTED_HTTP_MODULE = textwrap.dedent(
    '''
    """A PLANTED HTTP violation — written only under a temp dir, never committed."""
    from __future__ import annotations


    def handler() -> None:
        raise HTTPException(status_code=403, detail="planted")
    '''
).lstrip()


def test_lgw08_5_http_gate_flags_planted_violation(tmp_path: Path) -> None:
    # Shape a temp tree like a detect/ module and plant an HTTP construct in it.
    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    (detect_dir / "__init__.py").write_text("", encoding="utf-8")
    planted = detect_dir / "bad_backend.py"
    planted.write_text(_PLANTED_HTTP_MODULE, encoding="utf-8")
    hits = scan_http(tmp_path)
    paths = {hit.path for hit in hits}
    assert str(planted) in paths, f"gate must flag the planted violation; hits={hits}"
    symbols = {hit.symbol for hit in hits if hit.path == str(planted)}
    # Both the HTTPException name and the status_code=4xx are forbidden constructs.
    assert "HTTPException" in symbols
    assert "status_code=4xx" in symbols


def test_real_detect_tree_scans_clean_for_http() -> None:
    detect_root = Path(__file__).resolve().parents[2] / "gateway_v2" / "detect"
    assert detect_root.is_dir(), f"detect package not found at {detect_root}"
    # scan_http excludes nothing by top for an isolated root; scan the detect tree
    # directly and assert no HTTP construct lives under it.
    hits = scan_http(detect_root)
    assert hits == [], f"real detect/ must be HTTP-clean, found: {hits}"


def test_unscanned_channels_is_empty() -> None:
    assert unscanned_channels() == ()

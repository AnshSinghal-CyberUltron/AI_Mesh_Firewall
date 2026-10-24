"""GW16 task 5.7 property suite — the fail-closed contract across both scanning surfaces.

One property ↔ one test. A seeded :class:`random.Random` drives ≥10,000 iterations (NO
hypothesis); the async surface lifecycle runs via :func:`asyncio.run`. The property:

    *For any* input scan or decision that cannot be computed on an embeddings or moderations
    request, the surface REFUSES — it never forwards raw unscanned/undecided content and never
    returns an unscanned moderation result — makes ZERO provider calls, and
    ``PerRequestExports.snapshot().fail_open_total`` stays zero.

Every injected scan/decision fault is driven across the iterations:

* **embeddings — a detector that RAISES** ⇒ ``scan_failure`` fail-closed, zero provider calls,
  no embeddings ``data`` body (R4.6).
* **embeddings — a resolver whose ``decide`` RAISES** ⇒ ``scan_failure`` fail-closed, zero
  provider calls (``EmbeddingsSurface._input_scan`` wraps BOTH the detector run and the
  ``resolver.decide`` in one ``try/except``, so a raising resolver withholds exactly as a
  raising detector does), no embeddings ``data`` body (R4.6).
* **moderations — a detector that RAISES** ⇒ ``scan_failure`` fail-closed, zero provider calls,
  NO moderation ``results`` body — never a ``flagged:false`` unscanned result (R8.6).

The input is randomised every iteration (string vs list input, item count, canary position),
so the fail-closed property holds over varied inputs. Zero provider calls is asserted with a
counted :class:`~gateway_v2.dispatch.provider.StubProviderClient`; the pinned-zero series is
read through ``deps.exports.snapshot().fail_open_total`` (R4.6 / R8.6 / R10.3).

The harness (``MiscRoute`` deps, the ASGI ``receive``/``_Capture`` send, the counted provider,
the raising-detector shape) is the one the GW16 smoke tests use
(``test_gw16_embeddings_smoke.py`` + ``test_gw16_moderations_smoke.py``).
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain import Decision
from gateway_v2.domain.finding import Finding
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.app import empty_catalog_source
from gateway_v2.edge.chat_deps import InputDetector
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_misc import EmbeddingsSurface, MiscRoute, ModerationsSurface
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver, OutputResolver
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_ITERATIONS = 10_000


def _contract() -> ResourceContract:
    """A serviceable contract (the shared ``from_signals`` fixture shape)."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
    )


# --------------------------------------------------------------------------- #
# Test doubles (real StubProviderClient; permissive-but-pre-scan deps so the lifecycle
# actually REACHES the scan phase, where the injected fault bites).
# --------------------------------------------------------------------------- #


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    The fail-closed property asserts ``open_calls == 0`` on every iteration: a scan/decision
    that cannot be computed must never reach the provider.
    """

    __slots__ = ("open_calls",)

    def __init__(self) -> None:
        super().__init__(script=(), rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _FixedIdentity:
    """An ``IdentityResolver`` that resolves one canned tenant (so the scan phase is reached)."""

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


def _served_snapshot(plan: ExecutionPlan) -> ReplicaSnapshot:
    """A real :class:`ReplicaSnapshot` serving one canned plan (so the plan phase passes).

    The embeddings lifecycle resolves ``Surface.EMBEDDINGS`` on the served snapshot. A real
    ``ReplicaSnapshot`` over a ``PlanStore`` seeded with ``plan`` resolves cleanly, matching
    the ``MiscRoute.snapshot_source`` nominal type.
    """
    store = PlanStore()
    store.put(plan)
    snapshot = ReplicaSnapshot(store)
    snapshot.absorb(plan.org_id)
    return snapshot


class _AlwaysAdmit:
    """Admission that always admits, so the embeddings lifecycle reaches the input scan."""

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)

    def complete(self, request_id: str) -> None:
        del request_id


class _AlwaysBudget:
    """Budget that is always within budget, so the lifecycle reaches the input scan."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return Admitted(owner_id="gw16", request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _RaisingDetector:
    """A detector whose scan cannot be computed — it RAISES (uncomputable scan)."""

    __slots__ = ()

    def __call__(self, text: str) -> Sequence[Finding]:
        del text
        raise RuntimeError("detector blew up")


def _no_findings(text: str) -> Sequence[Finding]:
    del text
    return ()


class _RaisingResolver:
    """An ``OutputResolver`` whose ``decide`` cannot be computed — it RAISES.

    ``EmbeddingsSurface._input_scan`` wraps the detector run AND ``resolver.decide`` in one
    ``try/except``; a raising resolver therefore withholds the request with ``scan_failure``
    exactly as a raising detector does (the decision could not be computed, R4.6).
    """

    __slots__ = ()

    def decide(self, matches: Sequence[Finding]) -> Decision:
        del matches
        raise RuntimeError("resolver blew up")


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        org_id="gw16-org",
        epoch=1,
        sequence=1,
        content_hash="gw16" * 8,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _identity() -> TenantIdentity:
    return TenantIdentity(
        org_id="gw16-org",
        principal=Principal(
            key_id="gw16-key",
            org_id="gw16-org",
            rate_per_s=1.0,
            burst=1.0,
            feed_seq=0,
        ),
    )


# --------------------------------------------------------------------------- #
# A tiny ASGI receive/send harness (the GW16 smoke-test shape).
# --------------------------------------------------------------------------- #


def _receive_for(body: bytes) -> Callable[[], Awaitable[Mapping[str, object]]]:
    """An ASGI ``receive`` that delivers ``body`` as one terminal http.request event."""
    delivered = False

    async def receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class _Capture:
    """An ASGI ``send`` that captures the response start + body events."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.body: bytes = b""

    async def __call__(self, event: Mapping[str, object]) -> None:
        if event.get("type") == "http.response.start":
            self.status = int(event["status"])  # type: ignore[call-overload]
        elif event.get("type") == "http.response.body":
            chunk = event.get("body")
            if isinstance(chunk, (bytes, bytearray)):
                self.body += bytes(chunk)


_EMBEDDINGS_SCOPE: Mapping[str, object] = {
    "type": "http",
    "method": "POST",
    "path": "/v1/embeddings",
}
_MODERATIONS_SCOPE: Mapping[str, object] = {
    "type": "http",
    "method": "POST",
    "path": "/v1/moderations",
}


def _embeddings_deps(
    provider: _CountingProvider,
    *,
    detector: InputDetector,
    resolver: OutputResolver,
) -> MiscRoute:
    """Build a MiscRoute whose embeddings lifecycle reaches the input scan phase."""
    return MiscRoute(
        contract=_contract(),
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=empty_catalog_source,
        identity=_FixedIdentity(_identity()),
        admission=_AlwaysAdmit(),
        quota=_AlwaysBudget(),
        snapshot_source=lambda: _served_snapshot(_plan()),
        input_detector=detector,
        resolver=resolver,
        router=DispatchRouter(),
        transform=Transform(),
    )


def _moderations_deps(provider: _CountingProvider, *, detector: InputDetector) -> MiscRoute:
    """Build a MiscRoute whose moderations lifecycle reaches the (scan-only) scan phase."""
    return MiscRoute(
        contract=_contract(),
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=empty_catalog_source,
        identity=_FixedIdentity(_identity()),
        input_detector=detector,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


def _run_embeddings(deps: MiscRoute, body: bytes) -> _Capture:
    capture = _Capture()
    asyncio.run(EmbeddingsSurface(deps=deps).handle(_EMBEDDINGS_SCOPE, _receive_for(body), capture))
    return capture


def _run_moderations(deps: MiscRoute, body: bytes) -> _Capture:
    capture = _Capture()
    asyncio.run(
        ModerationsSurface(deps=deps).handle(_MODERATIONS_SCOPE, _receive_for(body), capture)
    )
    return capture


# --------------------------------------------------------------------------- #
# Random body generator — string vs list input, item count, canary position.
# --------------------------------------------------------------------------- #

_WORDS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "", "   ", "héllo")


def _random_input_body(rng: random.Random, *, with_model: bool) -> bytes:
    """A well-formed embeddings/moderations body with randomised input shape.

    The input is a bare string ~half the time, else a list of 1–5 items; a canary token is
    dropped at a random item position so the fail-closed property holds regardless of WHERE
    the (would-be) finding sits in the batch.
    """
    canary = f"GW16CANARY{rng.randrange(10_000)}"
    if rng.random() < 0.5:
        payload_input: object = f"{rng.choice(_WORDS)} {canary}"
    else:
        count = rng.randint(1, 5)
        items = [rng.choice(_WORDS) for _ in range(count)]
        items[rng.randrange(count)] = f"{rng.choice(_WORDS)} {canary} tail"
        payload_input = items
    payload: dict[str, object] = {"input": payload_input}
    if with_model:
        payload["model"] = rng.choice(("gw16-embed", "text-embedding-3-small", "my-mod"))
    return json.dumps(payload).encode()


def _is_success_embeddings_body(body: bytes) -> bool:
    """True iff ``body`` is a rendered embeddings SUCCESS body (has ``data`` + ``object:list``)."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(parsed, dict) and parsed.get("object") == "list" and "data" in parsed


def _has_moderation_results(body: bytes) -> bool:
    """True iff ``body`` carries a moderation ``results`` body (the thing R8.6 forbids on fault)."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(parsed, dict) and "results" in parsed


def _assert_scan_failure_refusal(capture: _Capture) -> None:
    """Assert the capture is the declared ``scan_failure`` withhold envelope (HTTP 403)."""
    assert capture.status == 403, capture.status
    error = json.loads(capture.body)["error"]
    assert error["type"] == "gateway_error", error
    assert error["code"] == "scan_failure", error


# --------------------------------------------------------------------------- #
# Property 7 — the one property ↔ one test (every injected fault, both surfaces).
# --------------------------------------------------------------------------- #


# Feature: chat-surface-gw16-misc, Property 7: Uncomputable scan or decision fails closed with FAIL_OPEN pinned at zero  # noqa: E501
# Validates: Requirements 4.6, 8.6
def test_uncomputable_scan_or_decision_fails_closed_fail_open_zero() -> None:
    """Property 7: an uncomputable scan/decision on embeddings or moderations fails closed.

    # Validates: Requirements 4.6, 8.6

    Over ≥10,000 seeded iterations, every injected scan/decision fault is driven across the
    two scanning surfaces with randomised inputs. On every iteration the surface REFUSES
    (``scan_failure`` — never a success body, never an unscanned moderation ``results`` body),
    makes ZERO counted provider calls, and ``exports.snapshot().fail_open_total`` is zero.
    """
    rng = random.Random(20260703)

    for _ in range(_ITERATIONS):
        # Rotate across the three injected faults so EVERY fault is driven over the run.
        fault = rng.randrange(3)

        if fault == 0:
            # embeddings — a detector that RAISES ⇒ scan_failure, zero provider calls, no data.
            provider = _CountingProvider()
            deps = _embeddings_deps(
                provider, detector=_RaisingDetector(), resolver=MinimalOutputResolver()
            )
            capture = _run_embeddings(deps, _random_input_body(rng, with_model=True))
            # Refused with the declared withhold code; NOT a success body (no raw forward, R4.6).
            _assert_scan_failure_refusal(capture)
            assert not _is_success_embeddings_body(capture.body)

        elif fault == 1:
            # embeddings — a resolver whose decide RAISES ⇒ scan_failure (one try/except wraps
            # detector + resolver), zero provider calls, no data (R4.6). Detector returns findings
            # (no-finding would still exercise resolve, but a raising resolver bites either way).
            provider = _CountingProvider()
            deps = _embeddings_deps(
                provider, detector=_no_findings, resolver=_RaisingResolver()
            )
            capture = _run_embeddings(deps, _random_input_body(rng, with_model=True))
            _assert_scan_failure_refusal(capture)
            assert not _is_success_embeddings_body(capture.body)

        else:
            # moderations — a detector that RAISES ⇒ scan_failure, zero provider calls, NO
            # results body (never a flagged:false unscanned result — R8.6).
            provider = _CountingProvider()
            deps = _moderations_deps(provider, detector=_RaisingDetector())
            capture = _run_moderations(deps, _random_input_body(rng, with_model=rng.random() < 0.5))
            _assert_scan_failure_refusal(capture)
            assert not _has_moderation_results(capture.body)

        # The two hard gates, asserted on EVERY iteration regardless of which fault fired:
        # zero provider contact, and the pinned-zero FAIL_OPEN series never incremented.
        assert provider.open_calls == 0
        assert deps.exports.snapshot().fail_open_total == 0

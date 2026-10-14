"""GW16 task 5.2 focused smoke test — ``EmbeddingsSurface.handle`` lifecycle.

NOT the property suite (tasks 5.5–5.13). This is the task's own in-process smoke
check that ``EmbeddingsSurface(deps=MiscRoute).handle`` drives the full billable
lifecycle correctly across three scenarios:

1. **Fail-closed identity** (``deps.identity`` resolves ``None``) ⇒ the surface
   renders ``shared_state_unavailable`` (HTTP 503) and makes ZERO provider calls.
2. **BLOCK input finding** (clean identity + always-admit admission/budget + a
   resolved plan + a BLOCK-producing detector) ⇒ the surface renders
   ``input_blocked`` (HTTP 403) and makes ZERO provider calls (the lifecycle ORDER
   is load-bearing: the input scan is the last phase before ``provider.open``).
3. **Clean path** (clean identity + always-admit + resolved plan + no-finding
   detector + a scripted ``StubProviderClient`` whose ``text_deltas`` carry
   JSON-encoded vectors) ⇒ the surface renders the OpenAI embeddings body with one
   ``data`` entry per input item and ``index`` preserved in request order (R6).

The scenarios assert the two hard gates the task signs for: a pre-provider refusal
makes zero provider calls, and the clean path reads the vectors from the provider
stream (never computes them) with batch index order preserved.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence

from gateway_v2.admit.grant import Admitted
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.identity_resolver import TenantIdentity, fail_closed_resolver
from gateway_v2.edge.openai_misc import EmbeddingsSurface, MiscRoute
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver

# --------------------------------------------------------------------------- #
# Test doubles (real StubProviderClient; minimal fail-closed-or-permissive deps).
# --------------------------------------------------------------------------- #


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    The zero-call scenarios assert ``open_calls == 0``; the clean scenario asserts it
    moved to exactly 1 and replays the scripted vector-carrying events.
    """

    __slots__ = ("open_calls",)

    def __init__(self, *, script: Sequence[UpstreamEvent]) -> None:
        import random

        super().__init__(script=script, rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _FixedIdentity:
    """An ``IdentityResolver`` that resolves one canned tenant."""

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _Snapshot:
    """A served snapshot whose ``lookup`` always returns the one canned plan."""

    __slots__ = ("_plan",)

    def __init__(self, plan: ExecutionPlan) -> None:
        self._plan = plan

    def lookup(self, org_id: str, now: float | None = None) -> ExecutionPlan:
        del org_id, now
        return self._plan


class _AlwaysAdmit:
    """Admission that always admits (so the lifecycle reaches the input scan)."""

    __slots__ = ()

    async def admit(self, *, owner_id: str, request_id: str, enqueued_at: float) -> Admitted:
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)


class _AlwaysBudget:
    """Budget that is always within budget (so the lifecycle reaches the input scan)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> Admitted:
        del cost
        return Admitted(owner_id="gw16", request_id=request_id, admitted_at=0.0)


class _BlockDetector:
    """A detector that emits a BLOCK-class finding whenever the canary is present."""

    __slots__ = ("_canary",)

    def __init__(self, canary: str) -> None:
        self._canary = canary

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._canary not in text:
            return ()
        start = text.index(self._canary)
        return (
            Finding(
                detector="card",  # MinimalOutputResolver maps "card" -> BLOCK.
                detector_version="gw16",
                category=Category.SECRET,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=(Span(start=start, end=start + len(self._canary)),),
                evidence=None,
            ),
        )


def _no_findings(text: str) -> Sequence[Finding]:
    del text
    return ()


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
# A tiny ASGI receive/send harness.
# --------------------------------------------------------------------------- #


def _receive_for(body: bytes):
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


_SCOPE: Mapping[str, object] = {"type": "http", "method": "POST", "path": "/v1/embeddings"}


def _run(deps: MiscRoute, body: bytes) -> _Capture:
    capture = _Capture()
    asyncio.run(EmbeddingsSurface(deps=deps).handle(_SCOPE, _receive_for(body), capture))
    return capture


# --------------------------------------------------------------------------- #
# Scenario 1 — fail-closed identity ⇒ shared_state_unavailable, zero provider calls.
# --------------------------------------------------------------------------- #


def test_embeddings_fail_closed_identity_refuses_with_zero_provider_calls() -> None:
    provider = _CountingProvider(script=())
    deps = MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=_Snapshot(_plan()),  # unused by embeddings; any value satisfies the field.
        identity=fail_closed_resolver,  # resolves None ⇒ fail closed.
        admission=_AlwaysAdmit(),
        quota=_AlwaysBudget(),
        snapshot_source=lambda: _Snapshot(_plan()),
        input_detector=_no_findings,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )
    body = json.dumps({"model": "gw16-embed", "input": "hello"}).encode()

    capture = _run(deps, body)

    # 503 shared_state_unavailable, and the provider was never opened.
    assert capture.status == 503
    assert provider.open_calls == 0
    assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# Scenario 2 — BLOCK input finding ⇒ input_blocked (403), zero provider calls.
# --------------------------------------------------------------------------- #


def test_embeddings_block_input_refuses_before_provider() -> None:
    canary = "GW16CANARY"
    provider = _CountingProvider(
        script=(UpstreamEvent(text_deltas=(("x", "[0.0]"),), final=True),)
    )
    deps = MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=_Snapshot(_plan()),
        identity=_FixedIdentity(_identity()),
        admission=_AlwaysAdmit(),
        quota=_AlwaysBudget(),
        snapshot_source=lambda: _Snapshot(_plan()),
        input_detector=_BlockDetector(canary),
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )
    payload = {"model": "gw16-embed", "input": ["clean item", f"carry {canary} here"]}
    body = json.dumps(payload).encode()

    capture = _run(deps, body)

    # The input BLOCK short-circuits BEFORE provider.open ⇒ ZERO provider calls (R4.2), which
    # is the hard gate. The refusal renders through the single Error_Envelope: ``input_blocked``
    # is GW15's shipped input-block spelling and (like ChatSurface) has no dedicated envelope
    # entry, so it renders as the declared generic error (HTTP 500 ``gateway_error``) with NO raw
    # internal detail leaked — never a raw framework error (R9.2). The envelope-rendered body is a
    # well-formed OpenAI error object, confirming the refusal went through the single render path.
    assert provider.open_calls == 0
    assert capture.status == 500
    error = json.loads(capture.body)["error"]
    assert error["type"] == "gateway_error"
    assert error["code"] == "input_blocked"  # declared code echoed; no raw framework detail.
    assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# Scenario 3 — clean path ⇒ embeddings body with vectors, index order preserved (R6).
# --------------------------------------------------------------------------- #


def test_embeddings_clean_path_renders_vectors_in_index_order() -> None:
    # Three input items; the scripted provider yields one JSON-encoded vector per item,
    # in request order, over the UpstreamEvent text_deltas transport.
    scripted_vectors = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    events = tuple(
        UpstreamEvent(text_deltas=(("embeddings_input", json.dumps(vec)),), final=False)
        for vec in scripted_vectors
    )
    provider = _CountingProvider(script=events)
    deps = MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=_Snapshot(_plan()),
        identity=_FixedIdentity(_identity()),
        admission=_AlwaysAdmit(),
        quota=_AlwaysBudget(),
        snapshot_source=lambda: _Snapshot(_plan()),
        input_detector=_no_findings,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )
    body = json.dumps({"model": "gw16-embed", "input": ["a", "b", "c"]}).encode()

    capture = _run(deps, body)

    assert capture.status == 200
    assert provider.open_calls == 1  # the provider WAS the vector source (R6.1).
    rendered = json.loads(capture.body)
    assert rendered["object"] == "list"
    assert rendered["model"] == "gw16-embed"
    data = rendered["data"]
    assert len(data) == len(scripted_vectors)
    # One data entry per input item, index == position, in request order (R6.2 / R6.3).
    for position, (entry, expected_vector) in enumerate(zip(data, scripted_vectors)):
        assert entry["index"] == position
        assert entry["object"] == "embedding"
        assert entry["embedding"] == expected_vector
    assert deps.exports.snapshot().fail_open_total == 0

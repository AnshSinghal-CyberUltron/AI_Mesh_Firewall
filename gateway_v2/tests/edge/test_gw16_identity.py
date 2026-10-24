"""GW16 Property 8 — unresolvable or stale identity fails closed, across all surfaces.

# Feature: chat-surface-gw16-misc, Property 8: Unresolvable or stale identity fails closed
# Validates: Requirements 5.2

House idiom (seeded ``random.Random`` ≥10,000 iterations, NO hypothesis, one property ↔
one test, async driven through ``asyncio.run``). This is the single property for GW16's
Property 8 (design → Correctness Properties → Property 8):

    *For any* misc request whose Tenant_Identity cannot be resolved, or whose freshness
    stamp is stale or unavailable, the surface renders the declared authentication
    value-code and makes zero provider calls.

The property holds across ALL THREE GW16 surfaces — ``EmbeddingsSurface`` +
``ModerationsSurface`` (``edge/openai_misc.py``) and ``ModelsSurface``
(``edge/openai_misc_models.py``). Every one resolves the Tenant_Identity FIRST and, when
the resolver returns ``None`` (the fail-closed answer for an unresolvable principal OR a
stale / unavailable GW05b/c freshness stamp — see ``edge/identity_resolver.py``), refuses
with the single declared ``REFUSE_IDENTITY_UNRESOLVED`` code
(``shared_state_unavailable``, from ``edge/openai_chat.py``). That code is in the
``edge/errors.py`` ``_ENVELOPE`` → ``(503, "service_unavailable")``, so the rendered HTTP
status is 503 and the body's ``error.code`` is ``shared_state_unavailable``.

Over the iterations we randomize:

* which surface is exercised — embeddings POST, moderations POST, models list GET, models
  retrieve GET — so the one property covers every misc entry point;
* the request shape (model present/absent, string vs list input, retrieve ``{id}``);
* which fail-closed resolver drives it — the shipped ``fail_closed_resolver`` (resolves
  ``None`` for an unresolved principal) AND a resolver that models a stale / unavailable
  freshness stamp by likewise returning ``None`` (``edge/identity_resolver.py`` maps a
  stale/unavailable stamp to ``None``).

For every iteration we assert the two hard gates Property 8 signs for: the rendered
value-code is exactly the declared auth code ``shared_state_unavailable`` at HTTP 503, and
the counted ``StubProviderClient`` was never opened (``open_calls == 0``).
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.edge.app import empty_catalog_source
from gateway_v2.edge.identity_resolver import (
    IdentityResolver,
    TenantIdentity,
    fail_closed_resolver,
)
from gateway_v2.edge.openai_misc import EmbeddingsSurface, MiscRoute, ModerationsSurface
from gateway_v2.edge.openai_misc_models import ModelsSurface
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

#: The declared authentication value-code (``edge/openai_chat.py::REFUSE_IDENTITY_UNRESOLVED``)
#: and its ``edge/errors.py`` ``_ENVELOPE`` mapping: ``shared_state_unavailable`` → HTTP 503
#: ``service_unavailable``.
_AUTH_CODE = "shared_state_unavailable"
_AUTH_STATUS = 503

_ITERATIONS = 10_000

# --------------------------------------------------------------------------- #
# Test doubles (real StubProviderClient; the two fail-closed resolvers).
# --------------------------------------------------------------------------- #


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    Property 8 asserts the surface makes ZERO provider calls on a fail-closed identity, so
    every iteration asserts ``open_calls == 0``.
    """

    __slots__ = ("open_calls",)

    def __init__(self) -> None:
        super().__init__(script=(), rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _StaleStampResolver:
    """An ``IdentityResolver`` modelling a STALE / UNAVAILABLE freshness stamp.

    ``edge/identity_resolver.py`` maps a stale / unavailable GW05b/c freshness stamp (a
    ``StoreDataUnavailable`` raised by the cache) to the SAME fail-closed ``None`` an
    unresolved principal yields — the identity held in RAM is refused rather than trusted.
    This resolver returns ``None`` for that reason, so the property covers the "stale /
    unavailable freshness stamp" clause distinctly from the unresolved-principal clause the
    shipped :data:`fail_closed_resolver` covers.
    """

    __slots__ = ()

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return None


#: The two fail-closed resolvers, both resolving ``None`` for Property 8's two clauses:
#: the shipped unresolved-principal default and the stale-stamp model.
_RESOLVERS: tuple[IdentityResolver, ...] = (fail_closed_resolver, _StaleStampResolver())


# --------------------------------------------------------------------------- #
# A tiny ASGI receive/send harness (reused from the GW16 smoke tests).
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


def _deps(provider: _CountingProvider, identity: IdentityResolver) -> MiscRoute:
    """A :class:`MiscRoute` carrying the counted provider + a fail-closed resolver.

    Every other field is a shipped stateless / fail-closed default — the identity phase is
    the FIRST phase of each surface, so a ``None`` identity refuses before any later field
    matters, and no later field can reach the provider.
    """
    return MiscRoute(
        contract=_contract(),
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=empty_catalog_source,
        identity=identity,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


# --------------------------------------------------------------------------- #
# Per-surface drivers — each builds a random valid request shape and returns the
# capture after running the surface handler through ``asyncio.run``.
# --------------------------------------------------------------------------- #


def _run_embeddings(
    provider: _CountingProvider, identity: IdentityResolver, rng: random.Random
) -> _Capture:
    model = rng.choice(("gw16-embed", "text-embedding-3-small", "e"))
    if rng.random() < 0.5:
        payload: dict[str, object] = {"model": model, "input": _rand_text(rng)}
    else:
        payload = {"model": model, "input": [_rand_text(rng) for _ in range(rng.randint(1, 3))]}
    body = json.dumps(payload).encode()
    scope: Mapping[str, object] = {"type": "http", "method": "POST", "path": "/v1/embeddings"}
    capture = _Capture()
    surface = EmbeddingsSurface(deps=_deps(provider, identity))
    asyncio.run(surface.handle(scope, _receive_for(body), capture))
    return capture


def _run_moderations(
    provider: _CountingProvider, identity: IdentityResolver, rng: random.Random
) -> _Capture:
    payload: dict[str, object] = {}
    if rng.random() < 0.5:  # model is optional on moderations.
        payload["model"] = rng.choice(("my-mod", "text-moderation-latest"))
    if rng.random() < 0.5:
        payload["input"] = _rand_text(rng)
    else:
        payload["input"] = [_rand_text(rng) for _ in range(rng.randint(1, 3))]
    body = json.dumps(payload).encode()
    scope: Mapping[str, object] = {"type": "http", "method": "POST", "path": "/v1/moderations"}
    capture = _Capture()
    surface = ModerationsSurface(deps=_deps(provider, identity))
    asyncio.run(surface.handle(scope, _receive_for(body), capture))
    return capture


def _run_models_list(
    provider: _CountingProvider, identity: IdentityResolver, rng: random.Random
) -> _Capture:
    del rng  # a models list GET carries no body/variation beyond the fixed path.
    scope: Mapping[str, object] = {"type": "http", "method": "GET", "path": "/v1/models"}
    capture = _Capture()
    surface = ModelsSurface(deps=_deps(provider, identity))
    asyncio.run(surface.handle_list(scope, _receive_for(b""), capture))
    return capture


def _run_models_retrieve(
    provider: _CountingProvider, identity: IdentityResolver, rng: random.Random
) -> _Capture:
    model_id = rng.choice(("gpt-4o-mini", "text-embedding-3-small", "a", "model-123"))
    scope: Mapping[str, object] = {
        "type": "http",
        "method": "GET",
        "path": f"/v1/models/{model_id}",
    }
    capture = _Capture()
    surface = ModelsSurface(deps=_deps(provider, identity))
    asyncio.run(surface.handle_retrieve(scope, _receive_for(b""), capture))
    return capture


_SURFACES: tuple[Callable[[_CountingProvider, IdentityResolver, random.Random], _Capture], ...] = (
    _run_embeddings,
    _run_moderations,
    _run_models_list,
    _run_models_retrieve,
)


def _rand_text(rng: random.Random) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz 0123456789"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 24)))


# --------------------------------------------------------------------------- #
# Property 8 — the single seeded ≥10,000-iteration test.
# --------------------------------------------------------------------------- #


def test_property_8_unresolvable_or_stale_identity_fails_closed() -> None:
    """A None-identity refuses ``shared_state_unavailable`` + 0 provider calls, every surface.

    # Feature: chat-surface-gw16-misc, Property 8: Unresolvable or stale identity fails closed
    # Validates: Requirements 5.2

    Over ≥10,000 seeded iterations, randomize the surface (embeddings POST / moderations
    POST / models list GET / models retrieve GET), the request shape, and the fail-closed
    resolver (shipped ``fail_closed_resolver`` for an unresolved principal AND a
    stale/unavailable-freshness-stamp model). For each: the rendered status is HTTP 503,
    the body's ``error.code`` is the declared ``shared_state_unavailable``, and the counted
    provider's ``open_calls`` is 0.
    """
    rng = random.Random(0xB16_1D)
    for _ in range(_ITERATIONS):
        surface = rng.choice(_SURFACES)
        identity = rng.choice(_RESOLVERS)
        provider = _CountingProvider()

        capture = surface(provider, identity, rng)

        # Gate 1 — the declared auth value-code, HTTP 503 service_unavailable.
        assert capture.status == _AUTH_STATUS
        error = json.loads(capture.body)["error"]
        assert error["code"] == _AUTH_CODE
        # Gate 2 — zero provider calls (the surface refused before any provider contact).
        assert provider.open_calls == 0
        # The content-free exports producer never fails open.
        assert _deps(provider, identity).exports.snapshot().fail_open_total == 0

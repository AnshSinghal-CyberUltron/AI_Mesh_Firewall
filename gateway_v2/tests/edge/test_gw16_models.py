"""GW16 task 5.11 — Property 13: unknown model id → not-found, zero provider calls.

Surface-level counterpart to the codec-level models-list property (task 2.9, Property 12).
This file FOCUSES on Property 13: ``ModelsSurface(deps=MiscRoute).handle_retrieve`` renders the
declared ``model_not_found`` value-code (HTTP ``404`` ``not_found_error``) for an id absent from
the identity's ``Allowed_Model_Catalog`` and makes ZERO provider calls — so the stock SDK's
``client.models.retrieve(<unknown>)`` raises ``openai.NotFoundError``.

The property is driven over a seeded ``random.Random`` for ≥10,000 iterations with NO hypothesis:
each iteration scripts a random catalog of :class:`~gateway_v2.edge.app.CatalogModel` and a random
``unknown_id`` GUARANTEED not in that catalog, drives ``handle_retrieve`` with scope path
``/v1/models/<unknown_id>`` behind a passing identity + a counted ``StubProviderClient``, and
asserts the 404 ``model_not_found`` render with ``open_calls == 0``.

A small complementary EXAMPLE rounds out the file (R7.2): a KNOWN id (a random member of the same
catalog) retrieves HTTP 200 with the OpenAI ``{id, object:"model"}`` body for the matching id and
likewise makes zero provider calls — Models carries no client text and opens no upstream.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.identity import Principal
from gateway_v2.edge.app import CatalogModel, CatalogSource
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_misc import MiscRoute
from gateway_v2.edge.openai_misc_models import ModelsSurface
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_ITERATIONS = 10_000
_SEED = 0x6716  # a plain fixed seed → deterministic replay.

# HTTP statuses asserted (response-status codes, not capacity-position literals).
_NOT_FOUND_STATUS = 404
_OK_STATUS = 200

# The identifier alphabet random model ids / unknown ids are drawn from.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"


# --------------------------------------------------------------------------- #
# Test doubles — a counted real StubProviderClient + a scripted CatalogSource +
# the GW16 _FixedIdentity shape (reused from the smoke tests).
# --------------------------------------------------------------------------- #


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    Models carries no client text and opens NO upstream (R7.4), so EVERY retrieve — hit or
    miss — must leave ``open_calls`` at zero.
    """

    __slots__ = ("open_calls",)

    def __init__(self) -> None:
        super().__init__(script=(), rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _FixedIdentity:
    """An ``IdentityResolver`` that resolves one canned tenant (NOT fail-closed).

    Reuses the GW16 smoke-test shape so the catalog lookup is actually reached — a
    fail-closed resolver would refuse at identity before the id is ever examined.
    """

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _ScriptedCatalog:
    """A :class:`CatalogSource` returning a scripted catalog for every identity.

    The tiny fake task 5.11 names: ``catalog_for(identity)`` ignores the identity and returns
    the exact tuple the iteration scripted, so the surface renders ``model_not_found`` for any
    id outside it and the retrieve body for any id inside it.
    """

    __slots__ = ("_models",)

    def __init__(self, models: tuple[CatalogModel, ...]) -> None:
        self._models = models

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]:
        del identity
        return self._models


def _identity() -> TenantIdentity:
    """One canned, resolvable tenant identity (the lookup is reached, not refused)."""
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
# A tiny ASGI receive/send harness (parity with the GW16 smoke tests).
# --------------------------------------------------------------------------- #


def _receive() -> Callable[[], Awaitable[Mapping[str, object]]]:
    """An ASGI ``receive`` — a GET carries no body the Models surface reads."""

    async def receive() -> Mapping[str, object]:
        return {"type": "http.disconnect"}

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


def _deps(provider: _CountingProvider, catalog: CatalogSource) -> MiscRoute:
    """Build the ``MiscRoute`` with the counted provider + scripted catalog + fixed identity."""
    return MiscRoute(
        contract=_contract(),
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=catalog,
        identity=_FixedIdentity(_identity()),
        input_detector=lambda text: (),
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


def _retrieve(deps: MiscRoute, model_id: str) -> _Capture:
    """Drive ``handle_retrieve`` for scope path ``/v1/models/<model_id>``."""
    scope: Mapping[str, object] = {
        "type": "http",
        "method": "GET",
        "path": f"/v1/models/{model_id}",
    }
    capture = _Capture()
    asyncio.run(ModelsSurface(deps=deps).handle_retrieve(scope, _receive(), capture))
    return capture


# --------------------------------------------------------------------------- #
# Random-catalog generators.
# --------------------------------------------------------------------------- #


def _random_id(rng: random.Random) -> str:
    """A random non-empty model id drawn from :data:`_ID_ALPHABET`."""
    length = rng.randint(1, 24)
    return "".join(rng.choice(_ID_ALPHABET) for _ in range(length))


def _random_catalog(rng: random.Random) -> tuple[CatalogModel, ...]:
    """A random catalog of unique-id :class:`CatalogModel` (0..8 entries)."""
    ids: set[str] = set()
    while len(ids) < rng.randint(0, 8):
        ids.add(_random_id(rng))
    return tuple(
        CatalogModel(id=mid, created=rng.randint(0, 1_900_000_000), owned_by=_random_id(rng))
        for mid in ids
    )


def _unknown_id(rng: random.Random, catalog: tuple[CatalogModel, ...]) -> str:
    """A random non-empty id GUARANTEED absent from ``catalog``."""
    known = {model.id for model in catalog}
    while True:
        candidate = _random_id(rng)
        if candidate not in known:
            return candidate


# --------------------------------------------------------------------------- #
# Property 13 — unknown id → model_not_found (404 not_found_error), zero provider calls.
# --------------------------------------------------------------------------- #

# Feature: chat-surface-gw16-misc, Property 13: Unknown model id yields not-found with zero
# provider calls
# Validates: Requirements 7.3
def test_unknown_model_id_yields_not_found_with_zero_provider_calls() -> None:
    """An id absent from the catalog renders ``model_not_found`` (404) with zero provider calls.

    For every random catalog + random unknown id, ``handle_retrieve`` must:

    * render HTTP ``404`` with ``error.type == "not_found_error"`` and
      ``error.code == "model_not_found"`` (the declared envelope entry task 3.1 added), so the
      stock SDK raises ``openai.NotFoundError``; and
    * make ZERO provider calls — Models carries no client text and opens no upstream (R7.4).
    """
    rng = random.Random(_SEED)
    for _ in range(_ITERATIONS):
        catalog = _random_catalog(rng)
        unknown = _unknown_id(rng, catalog)

        provider = _CountingProvider()
        deps = _deps(provider, _ScriptedCatalog(catalog))

        capture = _retrieve(deps, unknown)

        assert capture.status == _NOT_FOUND_STATUS
        error = json.loads(capture.body)["error"]
        assert error["type"] == "not_found_error"
        assert error["code"] == "model_not_found"
        # The hard gate: a not-found retrieve opens no provider.
        assert provider.open_calls == 0
        assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# Complementary EXAMPLE (R7.2) — a KNOWN id retrieves the model body, zero provider calls.
# --------------------------------------------------------------------------- #


def test_known_model_id_retrieves_model_body_with_zero_provider_calls() -> None:
    """A random known id → HTTP 200 ``{id, object:"model"}`` for the matching id, zero calls.

    Rounds out the file (the ≥10,000-iteration property above is Property 13): over several
    random catalogs, retrieving a random MEMBER of the catalog renders the OpenAI retrieve body
    with ``object == "model"`` and the matching ``id``, and still makes zero provider calls.
    """
    rng = random.Random(_SEED + 1)
    seen = 0
    while seen < 256:
        catalog = _random_catalog(rng)
        if not catalog:
            continue
        seen += 1
        model = rng.choice(catalog)

        provider = _CountingProvider()
        deps = _deps(provider, _ScriptedCatalog(catalog))

        capture = _retrieve(deps, model.id)

        assert capture.status == _OK_STATUS
        rendered = json.loads(capture.body)
        assert rendered["object"] == "model"
        assert rendered["id"] == model.id
        # Models carries no client text and opens no upstream, even on a hit (R7.4).
        assert provider.open_calls == 0
        assert deps.exports.snapshot().fail_open_total == 0

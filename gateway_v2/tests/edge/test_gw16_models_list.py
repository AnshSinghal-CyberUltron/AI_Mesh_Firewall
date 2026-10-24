"""GW16 task 2.9 — Property 12: the models-list render emits every allowed model.

Codec-level counterpart to the surface-level not-found property (task 5.11, Property 13, which
lives in ``test_gw16_models.py``). This file FOCUSES on Property 12: the ``GET /v1/models``
rendering path — ``ModelsSurface(deps=MiscRoute).handle_list`` over the
:class:`~gateway_v2.edge.wire.openai_misc.MiscResponseRenderer` — turns the identity's
``Allowed_Model_Catalog`` of K :class:`~gateway_v2.edge.app.CatalogModel` into a body that is
``object == "list"`` with ``data`` of EXACTLY K items, in catalog order, each item
``object == "model"`` carrying the catalog ``id`` / ``created`` / ``owned_by`` faithfully — no
reordering loss, no dropped or duplicated id. The empty catalog (K == 0) renders ``data == []``
with ``object == "list"``.

The property is driven over a seeded ``random.Random`` for ≥10,000 iterations with NO hypothesis:
each iteration scripts a random catalog whose size K ranges from 0 to large (so the empty case and
large catalogs are both covered), drives ``handle_list`` behind a passing identity + a counted
``StubProviderClient``, and asserts the list shape, the exact K-item count, the faithful per-item
fields, and strict catalog order. Models carries no client text and opens no upstream, so a passing
assertion also pins ``open_calls == 0`` and ``fail_open_total == 0`` (R7.4/R10).
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
_SEED = 0x120C  # a plain fixed seed → deterministic replay.

# HTTP status asserted (a response-status code, not a capacity-position literal).
_OK_STATUS = 200

# The identifier alphabet random model ids / owners are drawn from.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"

# Catalog-size range: 0 (empty) .. large, so the empty case and large catalogs are both exercised.
_MIN_CATALOG = 0
_MAX_CATALOG = 32


# --------------------------------------------------------------------------- #
# Test doubles — a counted real StubProviderClient + a scripted CatalogSource +
# the GW16 fixed identity shape (parity with test_gw16_models.py).
# --------------------------------------------------------------------------- #


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    Models carries no client text and opens NO upstream (R7.4), so a list render must leave
    ``open_calls`` at zero.
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

    A fail-closed resolver would refuse at identity before the catalog is ever read; this fixed
    resolver lets the list path actually render.
    """

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _ScriptedCatalog:
    """A :class:`CatalogSource` returning a scripted catalog for every identity.

    ``catalog_for(identity)`` ignores the identity and returns the exact ordered tuple the
    iteration scripted, so the render reflects precisely that catalog, in that order.
    """

    __slots__ = ("_models",)

    def __init__(self, models: tuple[CatalogModel, ...]) -> None:
        self._models = models

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]:
        del identity
        return self._models


def _identity() -> TenantIdentity:
    """One canned, resolvable tenant identity (the catalog lookup is reached, not refused)."""
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
# A tiny ASGI receive/send harness (parity with the GW16 models tests).
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


def _list(deps: MiscRoute) -> _Capture:
    """Drive ``handle_list`` for scope path ``/v1/models``."""
    scope: Mapping[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
    }
    capture = _Capture()
    asyncio.run(ModelsSurface(deps=deps).handle_list(scope, _receive(), capture))
    return capture


# --------------------------------------------------------------------------- #
# Random-catalog generator (0 .. large, unique ids).
# --------------------------------------------------------------------------- #


def _random_id(rng: random.Random) -> str:
    """A random non-empty model id / owner drawn from :data:`_ID_ALPHABET`."""
    length = rng.randint(1, 24)
    return "".join(rng.choice(_ID_ALPHABET) for _ in range(length))


def _random_catalog(rng: random.Random) -> tuple[CatalogModel, ...]:
    """A random catalog of unique-id :class:`CatalogModel`, size 0 .. :data:`_MAX_CATALOG`.

    The size is drawn across the whole range so K == 0 (empty) and large catalogs are both hit.
    Ids are unique within the catalog; order is the construction order the generator emits, which
    the render must preserve exactly (catalog order, no reordering).
    """
    target = rng.randint(_MIN_CATALOG, _MAX_CATALOG)
    ids: list[str] = []
    seen: set[str] = set()
    while len(ids) < target:
        candidate = _random_id(rng)
        if candidate in seen:
            continue
        seen.add(candidate)
        ids.append(candidate)
    return tuple(
        CatalogModel(
            id=mid,
            created=rng.randint(0, 1_900_000_000),
            owned_by=_random_id(rng),
        )
        for mid in ids
    )


# --------------------------------------------------------------------------- #
# Property 12 — the list render emits every allowed model, in order, faithfully.
# --------------------------------------------------------------------------- #

# Feature: chat-surface-gw16-misc, Property 12: Models list renders every allowed model
# Validates: Requirements 7.1
def test_models_list_renders_every_allowed_model() -> None:
    """``handle_list`` renders ``object == "list"`` with exactly K faithful items in order.

    For every random catalog of K models (K from 0 to large), ``handle_list`` must:

    * render HTTP ``200`` with a body whose ``object == "list"``;
    * carry a ``data`` array of EXACTLY K items — no dropped, no duplicated id;
    * each item ``object == "model"`` carrying the catalog ``id`` / ``created`` / ``owned_by``
      faithfully, in strict catalog order (no reordering loss); and
    * make ZERO provider calls with ``fail_open_total`` pinned at zero (R7.4/R10).

    The empty catalog (K == 0) renders ``data == []`` — ``object == "list"`` either way.
    """
    rng = random.Random(_SEED)
    for _ in range(_ITERATIONS):
        catalog = _random_catalog(rng)

        provider = _CountingProvider()
        deps = _deps(provider, _ScriptedCatalog(catalog))

        capture = _list(deps)

        assert capture.status == _OK_STATUS
        rendered = json.loads(capture.body)
        assert rendered["object"] == "list"

        data = rendered["data"]
        # Exactly K items — no dropped, no duplicated id.
        assert len(data) == len(catalog)
        rendered_ids = [item["id"] for item in data]
        assert len(set(rendered_ids)) == len(rendered_ids)

        # Strict catalog order + faithful per-item fields (id / created / owned_by).
        for expected, item in zip(catalog, data, strict=True):
            assert item["object"] == "model"
            assert item["id"] == expected.id
            assert item["created"] == expected.created
            assert item["owned_by"] == expected.owned_by

        # Models carries no client text and opens no upstream (R7.4), fail-open pinned zero.
        assert provider.open_calls == 0
        assert deps.exports.snapshot().fail_open_total == 0


# --------------------------------------------------------------------------- #
# Complementary EXAMPLE — the empty catalog renders an empty list (K == 0 edge).
# --------------------------------------------------------------------------- #


def test_empty_catalog_renders_empty_list() -> None:
    """An empty ``Allowed_Model_Catalog`` renders ``{object:"list", data:[]}`` with zero calls.

    Pins the K == 0 edge explicitly (the ≥10,000-iteration property above also hits it): the
    honest-empty catalog lists nothing — ``object == "list"`` with an empty ``data`` — and still
    opens no provider.
    """
    provider = _CountingProvider()
    deps = _deps(provider, _ScriptedCatalog(()))

    capture = _list(deps)

    assert capture.status == _OK_STATUS
    rendered = json.loads(capture.body)
    assert rendered["object"] == "list"
    assert rendered["data"] == []
    assert provider.open_calls == 0
    assert deps.exports.snapshot().fail_open_total == 0

# Feature: chat-surface-gw16-misc
# Validates: Requirements 1.2, 9.2, 11.3
"""GW16 ASGI matcher property + matcher-edge-case / boot-wiring unit tests (tasks 8.2 + 8.4).

Two task bodies share this file because both target the ``edge/app.py`` matcher wired in task 8.1
(the ``_ServingSkin._http`` prefix-match step + ``_match_prefix``) and the ``build_app`` boot path.

**Task 8.2 — Property 1 (``GET /v1/models/{id}`` path-parameter matching).** A seeded
``random.Random`` driven for ≥10,000 iterations (NO ``hypothesis``) over random request paths,
driving the REAL ``_ServingSkin`` matcher end-to-end over an in-process ASGI transport (the
``test_lgw15_app.py`` ``_Transport`` / ``_drive_http`` idiom, reused here). The matcher must:

* route ``GET /v1/models`` (exact) to the LIST handler — not retrieve, not ``not_implemented``;
* route ``GET /v1/models/<seg>`` (non-empty ``<seg>``, no ``/``) to the RETRIEVE handler, with the
  extracted id equal to ``<seg>`` — proven by the OBSERVABLE outcome against a scripted catalog: a
  ``<seg>`` present in the catalog → HTTP 200 ``{id, object:"model"}`` for exactly that id (so the
  matcher extracted exactly ``<seg>``), and a ``<seg>`` absent → 404 ``model_not_found`` (not 501
  ``not_implemented`` and not the 200 list), covering both extraction correctness and not-found;
* route any OTHER path (random non-models paths, and ``/v1/models/`` with an empty trailing
  segment) AWAY from the Models handlers — to its exact handler if one exists, else the declared
  501 ``not_implemented`` — i.e. NEVER to list or retrieve.

Because ``build_app`` does NOT expose a misc-dep injection seam (it hardcodes
``catalog=empty_catalog_source`` + ``renderer=MiscResponseRenderer()`` inside ``_build_misc_route``
— see the module note on :func:`_scripted_skin`), the scripted catalog + resolving identity needed
to make the extracted id observable are injected by building a scripted :class:`MiscRoute`,
assembling the two route tables through the REAL :func:`build_routes`, and swapping them onto the
REAL boot-built ``_ServingSkin`` via ``dataclasses.replace`` — so the exercised matcher
(``_http`` + ``_match_prefix``) is byte-for-byte the shipped one, only its route tables carry a
scripted catalog.

**Task 8.4 — matcher-edge-case + boot-wiring unit tests (NOT property).** Focused examples over
the same harness: the ``_http`` prefix step (positive ``/v1/models/<id>`` → retrieve), the
empty-suffix ``/v1/models/`` falling through to 501 ``not_implemented`` (NOT retrieve), an unknown
route → 501, an unmapped value-code → the generic 500 ``gateway_error`` body with NO raw leak
(R9.2), and the ``build_app`` boot wiring (R11.3): one :class:`MiscRoute` built with every dep,
accepting a :class:`StubProviderClient` + ``empty_catalog_source`` with no real socket/backend, the
three misc routes and responses resolving while mcp/rag/vector stay ``not_implemented``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random
from collections.abc import AsyncIterator, Mapping, Sequence

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import (
    ProviderClient,
    StubProviderClient,
    UpstreamEvent,
    UpstreamRequest,
)
from gateway_v2.domain import Finding
from gateway_v2.domain.identity import Principal
from gateway_v2.edge import errors
from gateway_v2.edge.app import (
    CatalogModel,
    CatalogSource,
    build_app,
    empty_catalog_source,
)
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_misc import MiscRoute
from gateway_v2.edge.routes import ChatRoute, build_routes
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_ITERATIONS = 10_000
_SEED = 0x6A1A  # a plain fixed seed → deterministic replay.

# HTTP statuses asserted (response-status codes, not capacity-position literals).
_OK_STATUS = 200
_GENERIC_STATUS = 500
_NOT_FOUND_STATUS = 404
_NOT_IMPLEMENTED_STATUS = 501

# The identifier alphabet random model ids / unknown segments are drawn from. No "/" — a path
# segment never contains the path separator — and non-empty by construction in the generators.
_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"

#: The three GW16 misc surfaces (wired live by task 7.1) + the GW15 chat routes.
_RESOLVED_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/embeddings"),
    ("GET", "/v1/models"),
    ("POST", "/v1/moderations"),
    ("POST", "/v1/chat/completions"),
    ("POST", "/v1/completions"),
    ("POST", "/v1/responses"),
)

#: Surfaces that stay declared ``not_implemented`` placeholders (R1.3 / R11.3).
_PLACEHOLDER_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/mcp"),
    ("POST", "/v1/rag/query"),
    ("POST", "/v1/vector/query"),
)


# --------------------------------------------------------------------------- #
# Fixtures: a serviceable contract + a scripted socket-free provider + a
# resolving identity + a scripted catalog (the GW16 test-double shapes).
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    """A serviceable contract (same ``from_signals`` fixture shape as the LGW15 app tests)."""
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


class _StubMetrics:
    """A minimal metrics registry: a fixed text exposition (plain registry, no directory)."""

    __slots__ = ()

    def exposition(self) -> str:
        return "# gateway_v2 metrics\n"


class _Clock:
    """A monotonic in-process clock stand-in (seconds), advanced on each read, never real."""

    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


def _no_error_findings(_frame: str) -> Sequence[Finding]:
    """The default error-frame scanner: no findings → a mid-stream error frame forwards clean."""
    return ()


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    The Models matcher paths carry no client text and open NO upstream, so no matcher iteration
    should ever move this counter — a useful hard gate the property / wiring tests can assert.
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

    A fail-closed resolver would refuse at identity before the matcher's extracted id is ever
    examined; the Models catalog lookup must actually be reached for the extraction to be
    observable, so this resolves one canned tenant for every scope.
    """

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _ScriptedCatalog:
    """A :class:`CatalogSource` returning a scripted catalog for every identity."""

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


def _scripted_misc(provider: ProviderClient, catalog: CatalogSource) -> MiscRoute:
    """Build the scripted :class:`MiscRoute`: counted provider + scripted catalog + fixed identity.

    Mirrors the ``test_gw16_models.py`` double exactly — a resolving identity so the Models catalog
    lookup is reached, and the scripted catalog so a retrieve's extracted id is observable. The
    remaining security deps keep their shipped fail-closed defaults (unused on the Models paths,
    which neither admit nor budget nor scan).
    """
    return MiscRoute(
        contract=_contract(),
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=catalog,
        identity=_FixedIdentity(_identity()),
        input_detector=lambda text: (),
        resolver=MinimalOutputResolver(),
    )


# --------------------------------------------------------------------------- #
# Driving the REAL _ServingSkin matcher with a scripted catalog (see the module
# docstring on why build_app cannot inject the catalog directly).
# --------------------------------------------------------------------------- #


def _build_app(provider: ProviderClient) -> object:
    """Assemble the REAL ASGI app with the injected ``provider`` (no socket)."""
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=_StubMetrics(),
        clock=_Clock(),
        error_scanner=_no_error_findings,
    )


def _boot_chatroute(app: object) -> ChatRoute:
    """Recover the single boot-built :class:`ChatRoute` from the assembled app.

    ``build_app`` wraps the route table in the frozen ``_ServingSkin``; the returned ASGI callable
    is the skin's bound ``asgi`` method, so ``app.__self__.routes`` is the mounted exact table and
    the chat handler's ``__self__`` is the boot ``ChatRoute`` — the exact route the boot path built.
    """
    skin = getattr(app, "__self__", None)
    routes = getattr(skin, "routes", None)
    assert isinstance(routes, Mapping), "could not recover the mounted route table from build_app"
    handler = routes[("POST", "/v1/chat/completions")]
    bound_self = getattr(handler, "__self__", None)
    assert isinstance(bound_self, ChatRoute), "could not recover the mounted ChatRoute from boot"
    return bound_self


def _scripted_skin(provider: ProviderClient, catalog: CatalogSource) -> object:
    """A REAL ``_ServingSkin`` whose matcher is exercised, with a scripted-catalog misc route.

    ``build_app`` has no misc-dep injection seam — ``_build_misc_route`` hardcodes
    ``catalog=empty_catalog_source`` + ``renderer=MiscResponseRenderer()`` — so a scripted catalog
    cannot be threaded through ``build_app`` directly (see the module docstring; this is the
    reported wiring finding). To still exercise the REAL ``_ServingSkin._http`` + ``_match_prefix``
    matcher over a scripted catalog, the boot app is assembled, its frozen ``_ServingSkin`` + boot
    ``ChatRoute`` recovered, a scripted :class:`MiscRoute` built, the two route tables assembled
    through the REAL :func:`build_routes`, and swapped onto the skin via ``dataclasses.replace`` —
    so the matcher code path, the operational wiring, and the ``ChatRoute`` are all the shipped
    boot values; only the mounted route tables carry the scripted catalog.
    """
    app = _build_app(provider)
    skin = getattr(app, "__self__", None)
    assert skin is not None, "could not recover the _ServingSkin from build_app"
    chat = _boot_chatroute(app)
    exact, prefix = build_routes(chat, _scripted_misc(provider, catalog))
    scripted = dataclasses.replace(skin, routes=exact, prefix_routes=prefix)
    return scripted.asgi


# --------------------------------------------------------------------------- #
# In-process ASGI transport (the test_lgw15_app.py / test_lgw12_app.py idiom).
# --------------------------------------------------------------------------- #


class _Transport:
    """A one-connection in-process ASGI transport: no socket, records every send event."""

    __slots__ = ("events", "_incoming")

    def __init__(self, *, body: bytes) -> None:
        self.events: list[Mapping[str, object]] = []
        self._incoming: list[Mapping[str, object]] = [
            {"type": "http.request", "body": body, "more_body": False},
            {"type": "http.disconnect"},
        ]

    @staticmethod
    def http_scope(method: str, path: str) -> dict[str, object]:
        return {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        }

    async def receive(self) -> Mapping[str, object]:
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))

    def status(self) -> int:
        starts = [e for e in self.events if e.get("type") == "http.response.start"]
        assert len(starts) == 1, f"expected exactly one response.start, got {len(starts)}"
        status = starts[0].get("status")
        assert isinstance(status, int), f"response.start carries no int status: {status!r}"
        return status

    def body_bytes(self) -> bytes:
        out = bytearray()
        for event in self.events:
            if event.get("type") != "http.response.body":
                continue
            body = event.get("body")
            if isinstance(body, (bytes, bytearray)):
                out.extend(body)
        return bytes(out)

    def json_body(self) -> object:
        return json.loads(self.body_bytes().decode("utf-8"))


def _drive(app: object, method: str, path: str, *, body: bytes = b"{}") -> _Transport:
    """Drive one in-process HTTP request through ``app`` and return the recording transport."""
    transport = _Transport(body=body)

    async def _run() -> None:
        # The ASGI callable is a bound method with the (scope, receive, send) signature.
        await app(transport.http_scope(method, path), transport.receive, transport.send)  # type: ignore[operator]

    asyncio.run(_run())
    return transport


def _is_not_implemented(transport: _Transport) -> bool:
    """True iff the response is the declared ``501 not_implemented`` Error_Envelope rendering."""
    if transport.status() != _NOT_IMPLEMENTED_STATUS:
        return False
    obj = transport.json_body()
    error = obj.get("error") if isinstance(obj, dict) else None
    return isinstance(error, dict) and error.get("type") == "not_implemented"


# --------------------------------------------------------------------------- #
# Random path generators (seeded random.Random; no hypothesis).
# --------------------------------------------------------------------------- #


def _random_seg(rng: random.Random) -> str:
    """A random NON-EMPTY path segment (no ``/``) drawn from :data:`_ID_ALPHABET`."""
    length = rng.randint(1, 24)
    return "".join(rng.choice(_ID_ALPHABET) for _ in range(length))


def _random_other_path(rng: random.Random) -> str:
    """A random path that is NEITHER ``/v1/models`` NOR a ``/v1/models/<seg>`` retrieve path.

    Draws from the known non-models routes, a handful of unmapped paths, and ``/v1/models/`` with an
    EMPTY trailing segment — every one of which must route AWAY from the Models list/retrieve
    handlers (to its exact handler if mapped, else the declared ``not_implemented``).
    """
    choices = (
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/responses",
        "/v1/embeddings",
        "/v1/moderations",
        "/v1/mcp",
        "/v1/rag/query",
        "/v1/vector/query",
        "/v1/models/",  # empty trailing segment → NOT a retrieve (falls through to 501).
        "/v1/model",  # near-miss prefix, not the models route.
        "/v1/modelss/" + _random_seg(rng),  # a different prefix entirely.
        "/" + _random_seg(rng),
        "/v2/models/" + _random_seg(rng),
        "/healthz",
    )
    return rng.choice(choices)


# --------------------------------------------------------------------------- #
# Task 8.2 — Property 1: GET /v1/models/{id} path-parameter matching.
# --------------------------------------------------------------------------- #

# Feature: chat-surface-gw16-misc, Property 1: GET /v1/models/{id} path-parameter matching
# Validates: Requirements 1.2
def test_models_path_parameter_matching_routes_list_retrieve_and_away() -> None:
    """The matcher routes ``/v1/models``, ``/v1/models/<seg>``, and other paths correctly (R1.2).

    Over a seeded ``random.Random`` for ≥10,000 iterations (no hypothesis), driving the REAL
    ``_ServingSkin`` matcher end-to-end over the in-process ASGI transport with a scripted catalog
    + a resolving identity. Each iteration asserts the THREE matcher obligations:

    1. ``GET /v1/models`` (exact) → the LIST handler: HTTP 200 ``{object:"list", data:[...]}`` with
       exactly one entry per scripted catalog model (never retrieve, never ``not_implemented``);
    2. ``GET /v1/models/<seg>`` (non-empty ``<seg>``) → the RETRIEVE handler with the extracted id
       equal to ``<seg>`` — proven by the outcome: a ``<seg>`` IN the catalog → HTTP 200
       ``{id, object:"model"}`` for exactly that id (so the matcher extracted precisely ``<seg>``),
       and a ``<seg>`` NOT in the catalog → 404 ``model_not_found`` (not the 501 placeholder, not
       the 200 list) — covering both extraction correctness and the not-found path;
    3. any OTHER path (random non-models paths + ``/v1/models/`` empty suffix) → AWAY from the
       Models handlers: never the 200 list and never a model retrieve body.

    A single counted provider is reused across the whole loop to assert the Models matcher paths
    open NO upstream (R7.4).
    """
    rng = random.Random(_SEED)
    provider = _CountingProvider()
    for _ in range(_ITERATIONS):
        # A scripted catalog with one KNOWN id (observable retrieve hit) + extra decoy models.
        known = _random_seg(rng)
        decoys = tuple(
            CatalogModel(id=_random_seg(rng), created=rng.randint(0, 1_900_000_000),
                         owned_by=_random_seg(rng))
            for _ in range(rng.randint(0, 3))
        )
        catalog_models = (
            CatalogModel(id=known, created=123, owned_by="gw16"),
            *decoys,
        )
        catalog = _ScriptedCatalog(catalog_models)
        app = _scripted_skin(provider, catalog)

        # (1) Exact /v1/models → LIST.
        listed = _drive(app, "GET", "/v1/models")
        assert listed.status() == _OK_STATUS, (
            f"GET /v1/models did not route to the list handler: {listed.status()}"
        )
        list_body = listed.json_body()
        assert isinstance(list_body, dict) and list_body.get("object") == "list", (
            "GET /v1/models did not render the models-list body"
        )
        data = list_body.get("data")
        assert isinstance(data, list) and len(data) == len(catalog_models), (
            "models-list did not carry one entry per scripted catalog model"
        )

        # (2a) /v1/models/<known seg> → RETRIEVE with the extracted id == <seg> (200, that id).
        hit = _drive(app, "GET", f"/v1/models/{known}")
        assert hit.status() == _OK_STATUS, (
            f"GET /v1/models/<known> did not route to retrieve: {hit.status()}"
        )
        hit_body = hit.json_body()
        assert isinstance(hit_body, dict) and hit_body.get("object") == "model", (
            "retrieve of a known id did not render the OpenAI model body"
        )
        assert hit_body.get("id") == known, (
            "the matcher did not extract exactly <seg> as the model id "
            f"(wanted {known!r}, got {hit_body.get('id')!r})"
        )

        # (2b) /v1/models/<unknown seg> → RETRIEVE miss → 404 model_not_found (reached retrieve).
        unknown = _random_seg(rng)
        while unknown in {m.id for m in catalog_models}:
            unknown = _random_seg(rng)
        miss = _drive(app, "GET", f"/v1/models/{unknown}")
        assert miss.status() == _NOT_FOUND_STATUS, (
            f"GET /v1/models/<unknown> did not reach retrieve (404): {miss.status()}"
        )
        miss_error = miss.json_body()
        assert (
            isinstance(miss_error, dict)
            and isinstance(miss_error.get("error"), dict)
            and miss_error["error"].get("code") == "model_not_found"
        ), "a retrieve miss did not render the declared model_not_found value-code"

        # (3) An OTHER path routes AWAY from the Models handlers (never list, never retrieve body).
        other = _random_other_path(rng)
        away = _drive(app, "GET", other)
        away_status = away.status()
        if away_status == _OK_STATUS:
            away_body = away.json_body()
            # A 200 here would only be legitimate for a non-models surface; it must NEVER be the
            # models list or a model retrieve body (that would mean the matcher mis-routed).
            if isinstance(away_body, dict):
                assert away_body.get("object") not in {"list", "model"}, (
                    f"path {other!r} mis-routed to a Models handler body"
                )

    # The Models matcher paths carry no client text and open no upstream (R7.4).
    assert provider.open_calls == 0, (
        f"a Models matcher path opened an upstream ({provider.open_calls} calls)"
    )


# --------------------------------------------------------------------------- #
# Task 8.4 — matcher-edge-case + boot-wiring UNIT tests (NOT property).
# --------------------------------------------------------------------------- #

# Feature: chat-surface-gw16-misc
# Validates: Requirements 9.2, 11.3
def test_http_prefix_step_routes_models_id_to_retrieve() -> None:
    """The ``_http`` prefix step routes ``GET /v1/models/<id>`` to retrieve (positive case).

    The exact-match miss on ``/v1/models/<id>`` falls through to ``_match_prefix``, which binds
    ``("GET", "/v1/models/")`` → retrieve; an id ABSENT from the (empty) catalog renders the
    declared ``model_not_found`` → HTTP 404 ``not_found_error`` (proving retrieve was reached, not
    the 501 placeholder).
    """
    app = _scripted_skin(_CountingProvider(), _ScriptedCatalog(()))
    transport = _drive(app, "GET", "/v1/models/gpt-does-not-exist")
    assert transport.status() == _NOT_FOUND_STATUS, (
        f"/v1/models/<id> did not reach retrieve (404): {transport.status()}"
    )
    body = transport.json_body()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict)
    assert body["error"].get("type") == "not_found_error"
    assert body["error"].get("code") == "model_not_found"


# Feature: chat-surface-gw16-misc
# Validates: Requirements 9.2, 11.3
def test_models_empty_suffix_falls_through_to_not_implemented() -> None:
    """``GET /v1/models/`` (empty trailing segment) → 501 ``not_implemented``, NOT retrieve.

    ``_match_prefix`` requires a NON-EMPTY trailing segment (``len(path) > len(prefix)``), so the
    bare prefix with an empty suffix does not match and falls through to the declared
    ``not_implemented`` placeholder — it must NOT render a model retrieve body or a 404.
    """
    app = _scripted_skin(_CountingProvider(), _ScriptedCatalog(()))
    transport = _drive(app, "GET", "/v1/models/")
    assert _is_not_implemented(transport), (
        f"/v1/models/ (empty suffix) did not fall through to 501 not_implemented: "
        f"{transport.status()}"
    )


# Feature: chat-surface-gw16-misc
# Validates: Requirements 9.2, 11.3
def test_unknown_route_renders_not_implemented() -> None:
    """An entirely unmapped ``(method, path)`` renders the declared 501 ``not_implemented``."""
    app = _scripted_skin(_CountingProvider(), _ScriptedCatalog(()))
    transport = _drive(app, "GET", "/v1/nope/" + "x" * 4)
    assert _is_not_implemented(transport), (
        f"an unknown route did not render 501 not_implemented: {transport.status()}"
    )


# Feature: chat-surface-gw16-misc
# Validates: Requirements 9.2
def test_unmapped_value_code_renders_generic_500_without_raw_leak() -> None:
    """An unmapped value-code → the declared generic 500 ``gateway_error`` body, no raw leak (R9.2).

    A code with no ``_ENVELOPE`` entry renders ``GENERIC_HTTP_STATUS`` (500) with a fixed
    ``gateway_error`` body; the raw code string MUST NOT appear in the human-readable ``message``
    (it rides only in the structured ``code`` field so the trace keeps the single spelling).
    """
    raw_code = "a_totally_unmapped_internal_value_code_xyz"
    response = errors.render_http(raw_code)
    assert response.status == _GENERIC_STATUS, (
        f"unmapped code did not render the generic {_GENERIC_STATUS}: {response.status}"
    )
    rendered = response.json()
    assert isinstance(rendered, dict) and isinstance(rendered.get("error"), dict)
    error = rendered["error"]
    assert error.get("type") == "gateway_error", "unmapped code did not render gateway_error"
    message = error.get("message")
    assert isinstance(message, str) and raw_code not in message, (
        "the raw value-code leaked into the human-readable message (R9.2)"
    )
    # The raw code must not leak into the body text anywhere except the structured code field.
    body_text = response.body.decode("utf-8")
    stripped = body_text.replace(json.dumps(raw_code), "", 1)
    assert raw_code not in stripped, "the raw value-code leaked outside the structured code field"


# Feature: chat-surface-gw16-misc
# Validates: Requirements 11.3
def test_build_app_wires_one_misc_route_with_every_dep_no_socket() -> None:
    """``build_app`` builds ONE :class:`MiscRoute` with every dep, no real socket/backend (R11.3).

    The boot path builds the single frozen :class:`MiscRoute` (recovered via the live misc
    handler's ``__self__``), carrying every injected dep: the :class:`StubProviderClient`
    in-process substitute (no socket), the honest-empty :data:`empty_catalog_source` (no real
    backend), the :class:`MiscResponseRenderer`, and the fail-closed security seams — a structural
    check that the deferred seams are the in-process substitutes.
    """
    provider = _CountingProvider()
    app = _build_app(provider)
    skin = getattr(app, "__self__", None)
    routes = getattr(skin, "routes", None)
    assert isinstance(routes, Mapping), "could not recover the mounted route table"

    # Recover the single boot MiscRoute from any live misc handler (all three share the one route).
    handler = routes[("GET", "/v1/models")]
    misc_self = getattr(handler, "__self__", None)
    deps = getattr(misc_self, "_deps", None)
    assert isinstance(deps, MiscRoute), "could not recover the boot MiscRoute from build_app"

    # The deferred seams are the in-process substitutes (no socket, no real catalog backend).
    assert deps.provider is provider, "boot did not inject the given StubProviderClient"
    assert isinstance(deps.provider, StubProviderClient), "provider is not the in-process stub"
    assert isinstance(deps.provider, ProviderClient), "stub does not satisfy ProviderClient"
    assert deps.catalog is empty_catalog_source, "boot did not use the honest-empty catalog default"
    assert deps.catalog.catalog_for(_identity()) == (), "the default catalog is not honest-empty"
    assert isinstance(deps.renderer, MiscResponseRenderer), "renderer not wired"
    # The security seams are present (callable / protocol-shaped), fail-closed by default.
    assert hasattr(deps.identity, "resolve"), "identity resolver seam missing"
    assert deps.admission is not None, "admission decider seam missing"
    assert deps.quota is not None, "budget decider seam missing"
    assert callable(deps.clock), "clock seam missing"
    # No real socket: the stub's only state is the scripted replay + the abort latch.
    assert set(StubProviderClient.__slots__) == {"_aborted", "rng", "script"}, (
        "StubProviderClient grew socket-bearing state"
    )


# Feature: chat-surface-gw16-misc
# Validates: Requirements 11.3
def test_build_app_misc_routes_resolve_while_deferred_surfaces_stay_not_implemented() -> None:
    """The misc routes resolve live while mcp/rag/vector stay 501 (R11.3).

    Over the REAL boot app (no scripted swap): the three misc surfaces, the two chat surfaces, and
    ``/v1/responses`` resolve to a live handler (they never render the ``not_implemented``
    placeholder), while the deferred mcp/rag/vector surfaces stay the declared 501.
    """
    app = _build_app(_CountingProvider())
    for method, path in _RESOLVED_ROUTES:
        transport = _drive(app, method, path)
        assert not _is_not_implemented(transport), (
            f"resolved route {(method, path)!r} rendered the not_implemented placeholder"
        )
    for method, path in _PLACEHOLDER_ROUTES:
        transport = _drive(app, method, path)
        assert transport.status() == _NOT_IMPLEMENTED_STATUS, (
            f"placeholder {(method, path)!r} did not render 501: {transport.status()}"
        )
        assert _is_not_implemented(transport), (
            f"placeholder {(method, path)!r} is not the not_implemented Error_Envelope"
        )

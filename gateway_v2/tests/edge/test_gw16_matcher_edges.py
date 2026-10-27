# Feature: chat-surface-gw16-misc
# Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 9.2, 11.3
"""GW16 prefix-route matcher edge cases + boot-wiring unit tests (task 8.4).

Example-based (plain ``assert``) unit tests — NOT the house property idiom — over the REAL
``edge/app.py`` serving skin matcher wired in task 8.1 (``_ServingSkin._http`` +
``_ServingSkin._match_prefix``, consulting the ``prefix_routes`` table) and the ``build_app``
boot path. The sibling ``test_gw16_app.py`` holds task 8.2's Property 1 + a first slice of 8.4;
THIS file pins the matcher's remaining edge cases with targeted examples:

* **Longest-prefix retrieve.** ``GET /v1/models/<id>`` (non-empty, no ``/``) resolves to the
  retrieve handler with the extracted id equal to ``<id>`` — proven by the observable outcome
  against a scripted catalog (a known id → 200 ``{id, object:"model"}`` for exactly that id).
* **List, not retrieve-with-empty-id.** ``GET /v1/models`` (exact) and ``GET /v1/models/``
  (bare prefix, empty trailing segment) both route to the LIST surface / fall through to the
  declared 501 ``not_implemented`` — never to retrieve with an empty id (which would list the
  whole catalog by accident).
* **Unknown path → declared not-found/501.** An entirely unmapped path falls through to the
  declared ``not_implemented`` (501) rendered through the single ``Error_Envelope`` — the
  behaviour GW15/GW16 shipped, not an invented mapping.
* **Exact-match precedence.** ``GET /v1/models`` resolves through the EXACT table (the list
  handler) and never through the prefix table, even though the prefix ``("GET", "/v1/models/")``
  shares the ``/v1/models`` stem — the exact miss is required before ``_match_prefix`` runs.
* **Segment-boundary safety.** A path that is a prefix of a registered route but not a full
  segment boundary (e.g. ``/v1/modelsX``) does NOT spuriously match the models surface — it falls
  through to the declared ``not_implemented``.
* **Boot wiring smoke.** ``build_app`` wires the misc route so ``POST /v1/embeddings``,
  ``POST /v1/moderations``, ``GET /v1/models``, and ``GET /v1/models/{id}`` are all reachable
  through the serving skin — each registered path dispatches to the intended surface, while the
  deferred mcp/rag/vector surfaces stay the declared 501 ``not_implemented``.

The matcher cannot inject a scripted catalog through ``build_app`` (``_build_misc_route``
hardcodes ``catalog=empty_catalog_source``), so to make the extracted id OBSERVABLE the boot app
is assembled, its frozen ``_ServingSkin`` + boot ``ChatRoute`` recovered, a scripted
:class:`MiscRoute` built, the two tables re-assembled through the REAL :func:`build_routes`, and
swapped onto the skin via ``dataclasses.replace`` — the exercised matcher (``_http`` +
``_match_prefix``) is byte-for-byte the shipped one, only its route tables carry the scripted
catalog. This is the SAME harness ``test_gw16_app.py`` documents.
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
from gateway_v2.edge.app import (
    CatalogModel,
    CatalogSource,
    build_app,
)
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_misc import MiscRoute
from gateway_v2.edge.routes import ChatRoute, build_routes
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# HTTP statuses asserted (response-status codes, not capacity-position literals).
_OK_STATUS = 200
_NOT_FOUND_STATUS = 404
_NOT_IMPLEMENTED_STATUS = 501


# --------------------------------------------------------------------------- #
# Fixtures: a serviceable contract + a socket-free counting provider + a
# resolving identity + a scripted catalog (the GW16 test-double shapes, matching
# the test_gw16_app.py / test_gw16_models.py doubles exactly).
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    """A serviceable contract (the LGW15 app-test ``from_signals`` fixture shape)."""
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

    The Models matcher paths carry no client text and open NO upstream, so no matcher-edge
    iteration should ever move this counter — a hard gate these edge-case tests assert.
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

    A resolving identity so the Models catalog lookup is reached (not refused), and the scripted
    catalog so a retrieve's extracted id is observable. The remaining security deps keep their
    shipped fail-closed defaults (unused on the Models paths, which neither admit nor budget nor
    scan).
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
# Driving the REAL _ServingSkin matcher with a scripted catalog.
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

    ``build_app`` has no misc-dep injection seam, so a scripted catalog cannot be threaded through
    it directly. To still exercise the REAL ``_ServingSkin._http`` + ``_match_prefix`` matcher over
    a scripted catalog, the boot app is assembled, its frozen ``_ServingSkin`` + boot ``ChatRoute``
    recovered, a scripted :class:`MiscRoute` built, the two tables re-assembled through the REAL
    :func:`build_routes`, and swapped onto the skin via ``dataclasses.replace`` — so the matcher
    code path, the operational wiring, and the ``ChatRoute`` are the shipped boot values; only the
    mounted route tables carry the scripted catalog.
    """
    app = _build_app(provider)
    skin = getattr(app, "__self__", None)
    assert skin is not None, "could not recover the _ServingSkin from build_app"
    chat = _boot_chatroute(app)
    exact, prefix = build_routes(chat, _scripted_misc(provider, catalog))
    scripted = dataclasses.replace(skin, routes=exact, prefix_routes=prefix)
    return scripted.asgi


# --------------------------------------------------------------------------- #
# In-process ASGI transport (the test_gw16_app.py / test_lgw15_app.py idiom).
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


def _one_known_catalog(known_id: str) -> _ScriptedCatalog:
    """A scripted catalog carrying exactly one model with ``known_id`` + two decoys."""
    return _ScriptedCatalog(
        (
            CatalogModel(id=known_id, created=123, owned_by="gw16"),
            CatalogModel(id="decoy-a", created=1, owned_by="gw16"),
            CatalogModel(id="decoy-b", created=2, owned_by="gw16"),
        )
    )


# --------------------------------------------------------------------------- #
# Matcher edge cases (plain-assert example unit tests).
# --------------------------------------------------------------------------- #


def test_longest_prefix_routes_models_id_to_retrieve_with_exact_id() -> None:
    """``GET /v1/models/<id>`` resolves to retrieve, extracting EXACTLY ``<id>`` (R1.2).

    The exact-match miss on ``/v1/models/<id>`` falls through to ``_match_prefix``, which binds the
    longest ``("GET", "/v1/models/")`` prefix → retrieve. A ``<id>`` present in the scripted
    catalog renders HTTP 200 ``{id, object:"model"}`` for EXACTLY that id, proving the matcher
    extracted precisely ``<id>`` (not a shorter/longer slice).
    """
    known = "gpt-4o-mini"
    app = _scripted_skin(_CountingProvider(), _one_known_catalog(known))
    transport = _drive(app, "GET", f"/v1/models/{known}")

    assert transport.status() == _OK_STATUS, (
        f"GET /v1/models/<id> did not route to retrieve: {transport.status()}"
    )
    body = transport.json_body()
    assert isinstance(body, dict) and body.get("object") == "model", (
        "retrieve of a known id did not render the OpenAI model body"
    )
    assert body.get("id") == known, (
        f"the matcher did not extract exactly <id> (wanted {known!r}, got {body.get('id')!r})"
    )


def test_models_id_with_segmented_value_extracts_full_trailing_segment() -> None:
    """A model id containing ``.``/``-``/``:`` is extracted whole as the trailing segment (R1.2).

    The retrieve id is the WHOLE segment after ``/v1/models/`` (no ``/``), so punctuation inside
    the id does not truncate the extraction — a known id with inner punctuation still renders a
    200 for exactly that id.
    """
    known = "ft:gpt-3.5-turbo:org-123:custom-model"  # no "/" → one path segment.
    app = _scripted_skin(_CountingProvider(), _one_known_catalog(known))
    transport = _drive(app, "GET", f"/v1/models/{known}")

    assert transport.status() == _OK_STATUS
    body = transport.json_body()
    assert isinstance(body, dict) and body.get("id") == known, (
        f"a punctuated model id was not extracted whole (got {body.get('id')!r})"
    )


def test_unknown_model_id_reaches_retrieve_and_renders_model_not_found() -> None:
    """``GET /v1/models/<unknown>`` reaches retrieve → 404 ``model_not_found`` (R1.2, R7.3).

    An id ABSENT from the scripted catalog proves the prefix matched (retrieve was reached) yet the
    lookup missed: the declared ``model_not_found`` renders HTTP 404 ``not_found_error`` — NOT the
    501 placeholder and NOT the 200 list.
    """
    app = _scripted_skin(_CountingProvider(), _one_known_catalog("present-id"))
    transport = _drive(app, "GET", "/v1/models/absent-id")

    assert transport.status() == _NOT_FOUND_STATUS, (
        f"GET /v1/models/<unknown> did not reach retrieve (404): {transport.status()}"
    )
    body = transport.json_body()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict)
    assert body["error"].get("type") == "not_found_error"
    assert body["error"].get("code") == "model_not_found"


def test_exact_models_routes_to_list_not_retrieve() -> None:
    """``GET /v1/models`` (no trailing id) → the LIST handler via the EXACT table (R1.1).

    The exact ``("GET", "/v1/models")`` key hits before ``_match_prefix`` ever runs, so the bare
    path renders the models-LIST body (one entry per catalog model), never a retrieve-with-empty-id
    and never the 501 placeholder.
    """
    app = _scripted_skin(_CountingProvider(), _one_known_catalog("m0"))
    transport = _drive(app, "GET", "/v1/models")

    assert transport.status() == _OK_STATUS, (
        f"GET /v1/models did not route to the list handler: {transport.status()}"
    )
    body = transport.json_body()
    assert isinstance(body, dict) and body.get("object") == "list", (
        "GET /v1/models did not render the models-list body (exact-table list route)"
    )
    data = body.get("data")
    assert isinstance(data, list) and len(data) == 3, (
        "models-list did not carry one entry per scripted catalog model"
    )


def test_exact_match_takes_precedence_over_prefix_table() -> None:
    """The exact ``/v1/models`` key wins though the prefix stem ``/v1/models/`` co-exists (R1.4).

    ``_match_prefix`` runs ONLY after an exact-match MISS, so an exact hit on ``("GET",
    "/v1/models")`` is never shadowed by the prefix ``("GET", "/v1/models/")`` entry — the response
    is the list body (object == "list"), never a retrieve body (object == "model").
    """
    app = _scripted_skin(_CountingProvider(), _one_known_catalog("m0"))
    transport = _drive(app, "GET", "/v1/models")

    body = transport.json_body()
    assert isinstance(body, dict), "GET /v1/models did not render a JSON object"
    assert body.get("object") == "list", (
        "exact /v1/models was shadowed by the prefix table (rendered a non-list body)"
    )
    assert body.get("object") != "model", (
        "exact /v1/models mis-routed to the retrieve handler (object == 'model')"
    )


def test_empty_trailing_segment_falls_through_to_not_implemented() -> None:
    """``GET /v1/models/`` (empty trailing segment) → 501 ``not_implemented``, NOT retrieve (R1.2).

    ``_match_prefix`` requires a NON-EMPTY trailing segment (``len(path) > len(prefix)``), so the
    bare prefix with an empty suffix does not match the retrieve handler and falls through to the
    declared ``not_implemented`` placeholder — it must NOT render a model retrieve body, a list, or
    a 404.
    """
    app = _scripted_skin(_CountingProvider(), _one_known_catalog("m0"))
    transport = _drive(app, "GET", "/v1/models/")

    assert _is_not_implemented(transport), (
        f"/v1/models/ (empty suffix) did not fall through to 501 not_implemented: "
        f"{transport.status()}"
    )


def test_non_segment_boundary_prefix_does_not_spuriously_match() -> None:
    """``/v1/modelsX`` (prefix of a route, not a segment boundary) → 501, not the models surface.

    ``/v1/models`` is a proper prefix of ``/v1/modelsX`` only as a STRING, not at a path-segment
    boundary (there is no ``/`` after ``/v1/models``). The exact table has no ``("GET",
    "/v1/modelsX")`` key, and the prefix table's ``/v1/models/`` is NOT a prefix of ``/v1/modelsX``
    (``X`` != ``/``), so the matcher must not spuriously route it to the models list or retrieve —
    it falls through to the declared ``not_implemented``.
    """
    app = _scripted_skin(_CountingProvider(), _one_known_catalog("m0"))
    transport = _drive(app, "GET", "/v1/modelsX")

    assert _is_not_implemented(transport), (
        f"/v1/modelsX spuriously matched the models surface instead of 501: {transport.status()}"
    )
    body = transport.json_body()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict)
    # Hard guard: it is neither a list nor a model body.
    assert body.get("object") not in {"list", "model"}, (
        "/v1/modelsX leaked a models-surface body"
    )


def test_models_double_slash_suffix_routes_retrieve_with_slashed_id() -> None:
    """``GET /v1/models//`` matches the prefix with a non-empty (``/``) suffix → retrieve miss.

    ``/v1/models//`` has ``len(path) > len("/v1/models/")`` with a trailing ``/`` as the extracted
    id, which no catalog contains → the retrieve path renders ``model_not_found`` (404), proving the
    matcher routed to retrieve (not list, not 501) on a non-empty suffix.
    """
    app = _scripted_skin(_CountingProvider(), _one_known_catalog("m0"))
    transport = _drive(app, "GET", "/v1/models//")

    assert transport.status() == _NOT_FOUND_STATUS, (
        f"/v1/models// did not reach retrieve (404): {transport.status()}"
    )
    body = transport.json_body()
    assert isinstance(body, dict) and isinstance(body.get("error"), dict)
    assert body["error"].get("code") == "model_not_found"


def test_unknown_path_falls_through_to_declared_not_implemented() -> None:
    """An entirely unmapped ``(method, path)`` → the declared 501 ``not_implemented`` (shipped).

    Neither the exact table nor the prefix table carries the path, so the matcher renders the
    declared ``not_implemented`` through the single ``Error_Envelope`` — the GW15/GW16 shipped
    fall-through, not an invented mapping.
    """
    app = _scripted_skin(_CountingProvider(), _one_known_catalog("m0"))
    transport = _drive(app, "GET", "/v1/does-not-exist")

    assert _is_not_implemented(transport), (
        f"an unknown path did not render 501 not_implemented: {transport.status()}"
    )


def test_models_matcher_paths_open_no_upstream() -> None:
    """Every Models matcher path (list / retrieve hit / miss / empty / near-miss) opens 0 upstream.

    Models is a catalog lookup — no client text, no provider (R7.4). A single counted provider is
    reused across every matcher-edge path above; none may move the counter.
    """
    provider = _CountingProvider()
    catalog = _one_known_catalog("present-id")

    for method, path in (
        ("GET", "/v1/models"),
        ("GET", "/v1/models/present-id"),
        ("GET", "/v1/models/absent-id"),
        ("GET", "/v1/models/"),
        ("GET", "/v1/modelsX"),
        ("GET", "/v1/does-not-exist"),
    ):
        _drive(_scripted_skin(provider, catalog), method, path)

    assert provider.open_calls == 0, (
        f"a Models matcher path opened an upstream ({provider.open_calls} calls)"
    )


# --------------------------------------------------------------------------- #
# Boot-wiring smoke: every registered misc path dispatches to its surface.
# --------------------------------------------------------------------------- #


def test_build_app_wires_all_misc_paths_reachable_through_the_skin() -> None:
    """``build_app`` wires the misc route so all four misc paths are reachable (R1.1/R1.2/R11.3).

    A boot smoke over the REAL boot app (no scripted swap). The boot ``MiscRoute`` carries the
    shipped FAIL-CLOSED ``identity`` default, so every misc surface refuses before reaching its
    catalog/scan/provider — but the point of this smoke is DISPATCH: each registered misc path must
    dispatch to its INTENDED surface rather than the ``not_implemented`` placeholder. A surface
    refusal is NOT the 501 placeholder (a different value-code / status), so "not the placeholder"
    across ``POST /v1/embeddings``, ``POST /v1/moderations``, ``GET /v1/models`` (list), and
    ``GET /v1/models/{id}`` (retrieve) proves all four are wired live and reachable through the
    matcher. (The id-extraction + catalog-hit path with a resolving identity is asserted by the
    scripted-skin tests above.)
    """
    app = _build_app(_CountingProvider())

    for method, path in (
        ("POST", "/v1/embeddings"),
        ("POST", "/v1/moderations"),
        ("GET", "/v1/models"),
        ("GET", "/v1/models/anything"),  # exact miss → _match_prefix → retrieve surface.
    ):
        transport = _drive(app, method, path)
        assert not _is_not_implemented(transport), (
            f"misc path {(method, path)!r} rendered the not_implemented placeholder — not wired"
        )
        # A dispatched surface answers with its own value-code shape (the single Error_Envelope),
        # never the 501 placeholder — so a reachable surface is proven by the non-placeholder reply.
        assert transport.status() != _NOT_IMPLEMENTED_STATUS, (
            f"misc path {(method, path)!r} fell through to 501 instead of a surface"
        )


def test_build_app_deferred_surfaces_stay_not_implemented() -> None:
    """mcp/rag/vector stay the declared 501 ``not_implemented`` after boot (R1.3/R11.3).

    The matcher must not accidentally resolve a deferred surface through the prefix table or an
    unknown-path fall-through to anything but the declared placeholder. ``/v1/responses`` is live
    since the GW15 close-out, so it is not in this set.
    """
    app = _build_app(_CountingProvider())
    for method, path in (
        ("POST", "/v1/mcp"),
        ("POST", "/v1/rag/query"),
        ("POST", "/v1/vector/query"),
    ):
        transport = _drive(app, method, path)
        assert transport.status() == _NOT_IMPLEMENTED_STATUS, (
            f"deferred {(method, path)!r} did not render 501: {transport.status()}"
        )
        assert _is_not_implemented(transport), (
            f"deferred {(method, path)!r} is not the not_implemented Error_Envelope"
        )

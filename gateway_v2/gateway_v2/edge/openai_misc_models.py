"""The ``GET /v1/models`` (list) + ``GET /v1/models/{id}`` (retrieve) surface (GW16).

``edge/openai_misc_models.py`` is a SIBLING of ``edge/openai_misc.py`` — it holds the single
:class:`ModelsSurface` handler while ``edge/openai_misc.py`` holds :class:`MiscRoute` +
``EmbeddingsSurface`` + ``ModerationsSurface`` + the two shared render helpers
(:func:`~gateway_v2.edge.openai_misc._refuse` / :func:`~gateway_v2.edge.openai_misc._emit_json`).
The split exists for exactly ONE reason: ``check_sizes`` caps every module at
``MAX_MODULE_LINES = 800`` and ``edge/openai_misc.py`` is already at that bound, so adding the
Models surface there would breach the gate. This is a no-behaviour-change structural placement —
the same split pattern GW15 used (``asgi_sse.py`` from ``routes.py``, ``series_count.py`` from
``publisher.py``, and ``edge/wire/openai_misc.py`` from ``edge/wire/openai.py``). Both modules stay
comfortably under the gate.

**ONE render path, REUSED (R9).** ``ModelsSurface`` renders every refusal through the shared
:func:`~gateway_v2.edge.openai_misc._refuse` (the one value-code → ``edge/errors``
``Error_Envelope`` path) and every 200 body through the shared
:func:`~gateway_v2.edge.openai_misc._emit_json` (the one ``application/json`` ``HTTPResponse``
path) — the SAME two helpers ``EmbeddingsSurface`` / ``ModerationsSurface`` use. They are imported
(underscore names explicitly) rather than re-spelled so the three surfaces never disagree on how a
body or a refusal leaves the gateway. The import direction is one-way: this module imports
``edge/openai_misc.py``; ``edge/openai_misc.py`` must NEVER import this module (parity with
``routes.py -> chat_deps``).

**Models is a catalog lookup — NO scan, NO provider (R7.4/R7.5).** Unlike embeddings (billable,
full lifecycle) and moderations (scan-only), Models carries no client text to scan and has no
upstream to call: it resolves the Tenant_Identity (``None`` ⇒ fail-closed
``shared_state_unavailable`` — R7.6), reads the ``Allowed_Model_Catalog`` from the injected
:class:`~gateway_v2.edge.app.CatalogSource` (``deps.catalog.catalog_for(identity)`` — R7.4), and
renders the OpenAI list / retrieve body. It runs NO ``input_detector`` and opens NO
``ProviderClient``; a models request therefore makes zero detector and zero provider calls by
construction.

**``{id}`` extraction is pure string work (Decision 2).** The retrieve path is ``/v1/models/<id>``,
so :meth:`ModelsSurface.handle_retrieve` reads ``scope["path"]`` and takes the trailing segment
(``path[len("/v1/models/"):]``) — ``edge`` string work, no new routing machinery. The prefix matcher
(task 8.1) only routes a NON-EMPTY trailing segment here, but the handler is defensive: an empty id
falls through to ``model_not_found`` so no malformed path ever lists the whole catalog by accident.

**Fail closed, content-free exports, ``fail_open_total`` pinned 0 (R10).** Every terminal outcome
(identity-fail / list / retrieve-hit / retrieve-miss) populates nothing that carries raw request or
response content and never increments ``fail_open_total`` — the ``PerRequestExports`` producer has
no fail-open increment path, and Models has no scan to withhold on, so the surface simply never
touches the fail-open counter (R10.3). The ``model_id`` is a routing identifier the client itself
sent, not scanned content, so echoing it in a ``model_not_found`` render carries no secret.

**No capacity literal, no module-level mutable.** The only module-level constants are the response
status / media-type / header-name / path-prefix response-shape strings (codes, not
capacity-position literals — the serving bounds live in ``runtime/resources.py``); there is no
module-level list / dict / set.
"""

from __future__ import annotations

from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.edge.openai_misc import MiscRoute, _emit_json, _refuse
from gateway_v2.edge.outcome_metrics import observe_tenant
from gateway_v2.edge.wire.openai_misc import MODEL_NOT_FOUND

__all__ = ("ModelsSurface",)


# --------------------------------------------------------------------------- #
# Path-prefix for the retrieve {id} extraction (Decision 2) — a response-shape
# string, NOT a capacity-position literal (the serving bounds live in
# ``runtime/resources.py``). ``_MODELS_PREFIX`` is the one place the prefix is
# spelled, so the prefix-matcher binding (task 8.1) and the ``{id}`` slice here
# agree by construction.
# --------------------------------------------------------------------------- #
_SCOPE_PATH_KEY = "path"
_MODELS_PREFIX = "/v1/models/"


class ModelsSurface:
    """``GET /v1/models`` (list) + ``GET /v1/models/{id}`` (retrieve): identity → catalog → render.

    Built per request (``ModelsSurface(deps=misc)``) over the frozen
    :class:`~gateway_v2.edge.openai_misc.MiscRoute`, so the surface carries no mutable state.
    Catalog-only: it resolves the tenant identity, reads the ``Allowed_Model_Catalog`` from the
    injected :class:`~gateway_v2.edge.app.CatalogSource`, and renders the OpenAI list / retrieve
    body — it runs NO input scan (R7.5) and opens NO provider (R7.4). An unresolvable identity
    fails CLOSED with ``shared_state_unavailable`` (R7.6); an id absent from the catalog renders
    ``model_not_found`` → HTTP 404 ``not_found_error`` so the stock SDK raises
    ``openai.NotFoundError`` (R7.3). Every terminal outcome renders through the single
    ``edge/errors`` envelope (or the one 200 JSON path) and populates the content-free
    :class:`~gateway_v2.runtime.stream_metrics.PerRequestExports`, ``fail_open_total`` pinned at
    zero (R10).
    """

    __slots__ = ("_deps",)

    def __init__(self, *, deps: MiscRoute) -> None:
        """Construct over the injected :class:`MiscRoute` dependency bundle."""
        self._deps = deps

    async def handle_list(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """Serve ``GET /v1/models`` — list every model in the identity's catalog (R7.1, R10).

        Resolves the Tenant_Identity (``None`` / stale / unavailable ⇒ fail-closed
        ``shared_state_unavailable`` via the shared :func:`_refuse` path — R7.6), reads the
        ordered ``Allowed_Model_Catalog`` with ``deps.catalog.catalog_for(identity)`` (R7.4), and
        emits ``{object:"list", data:[{id, object:"model", ...}]}`` in catalog order through the
        shared :func:`_emit_json` path (R7.1). Runs NO input scan and opens NO provider (R7.4/R7.5),
        so the request makes zero detector and zero provider calls. The ``receive`` channel is not
        drained — a ``GET`` carries no body the surface reads.
        """
        del receive  # GET /v1/models carries no body the surface reads.
        from gateway_v2.edge.openai_chat import REFUSE_IDENTITY_UNRESOLVED

        identity = await self._deps.identity.resolve(scope)
        if identity is None:
            await _refuse(send, REFUSE_IDENTITY_UNRESOLVED, self._deps.observer)
            return
        observe_tenant(self._deps.observer, identity.org_id)

        models = self._deps.catalog.catalog_for(identity)
        await _emit_json(send, self._deps.renderer.models_list_body(models))

    async def handle_retrieve(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        """Serve ``GET /v1/models/{id}`` — one model, or ``model_not_found`` (R7.2/R7.3, R10).

        Resolves the Tenant_Identity (``None`` ⇒ fail-closed ``shared_state_unavailable`` — R7.6),
        extracts the ``{id}`` from ``scope["path"]`` as the trailing segment after
        :data:`_MODELS_PREFIX` (pure string work — Decision 2), and looks it up in the identity's
        ``Allowed_Model_Catalog`` (``deps.catalog.catalog_for(identity)`` — R7.4). A present id
        renders ``{id, object:"model", ...}`` through the shared :func:`_emit_json` path (R7.2); an
        absent id (or a defensive empty id the prefix matcher would not route here) renders
        ``model_not_found`` through the shared :func:`_refuse` path → HTTP 404 ``not_found_error``
        so the stock SDK raises ``openai.NotFoundError`` (R7.3). Runs NO input scan and opens NO
        provider (R7.4/R7.5); the ``receive`` channel is not drained.
        """
        del receive  # GET /v1/models/{id} carries no body the surface reads.
        from gateway_v2.edge.openai_chat import REFUSE_IDENTITY_UNRESOLVED

        identity = await self._deps.identity.resolve(scope)
        if identity is None:
            await _refuse(send, REFUSE_IDENTITY_UNRESOLVED, self._deps.observer)
            return
        observe_tenant(self._deps.observer, identity.org_id)

        model_id = self._extract_model_id(scope)
        if not model_id:
            # Defensive: the prefix matcher (task 8.1) only routes a non-empty trailing segment
            # here, but an empty id must never list the whole catalog by accident — fail closed to
            # a not-found rather than a surprise list.
            await _refuse(send, MODEL_NOT_FOUND, self._deps.observer)
            return

        for model in self._deps.catalog.catalog_for(identity):
            if model.id == model_id:
                await _emit_json(send, self._deps.renderer.model_retrieve_body(model))
                return
        await _refuse(send, MODEL_NOT_FOUND, self._deps.observer)

    @staticmethod
    def _extract_model_id(scope: ASGIScope) -> str:
        """Extract the ``{id}`` trailing segment from the request path (Decision 2). Pure.

        Reads ``scope["path"]`` (a plain ``str`` in an ASGI ``http`` scope; a non-string / absent
        value yields ``""`` so the caller fails closed to ``model_not_found``) and returns the
        segment after :data:`_MODELS_PREFIX`. A path that does not carry the prefix — which the
        prefix matcher would not route here — yields ``""``. No I/O, no security decision: the id
        is a routing identifier, not scanned content.
        """
        path = scope.get(_SCOPE_PATH_KEY)
        if not isinstance(path, str) or not path.startswith(_MODELS_PREFIX):
            return ""
        return path[len(_MODELS_PREFIX):]

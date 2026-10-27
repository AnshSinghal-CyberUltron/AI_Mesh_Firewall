# Feature: chat-surface-gw16-misc
# Validates: Requirements 1.1, 1.3
"""Route-table wiring unit tests for the GW16 misc surfaces (task 7.2).

Example/unit tests (NO >=10k property loop, NO ``hypothesis``) over
:func:`gateway_v2.edge.routes.build_routes`, which GW16 widened to
``build_routes(chat, misc) -> tuple[RouteTable, RouteTable]`` returning ``(exact, prefix)``.
These assert the *wiring* the task spells out:

* the three misc EXACT keys bind the REAL GW16 handlers (``EmbeddingsSurface.handle`` /
  ``ModelsSurface.handle_list`` / ``ModerationsSurface.handle``), NOT the ``_placeholder``
  not-implemented handler (R1.1). ``build_routes`` binds each as a bound method
  (``Surface(deps=misc).handle``), so ``handler.__self__`` is the surface instance and
  ``handler.__func__`` is the class function; the ``_placeholder`` handler is a plain closure
  with no ``__self__`` — the precise, fast structural discriminator.
* the ``prefix`` table binds exactly ``("GET", "/v1/models/")`` to
  :meth:`ModelsSurface.handle_retrieve` (R1.2) — same bound-method structural check.
* the three remaining placeholders (``/v1/mcp``, ``/v1/rag/query``, ``/v1/vector/query``)
  resolve to the declared ``501 not_implemented`` ``Error_Envelope`` when driven over the
  in-process ASGI transport (R1.3).
* both returned tables are function-local ``Mapping`` values (``build_routes`` returns a 2-tuple
  of Mappings — the no-module-mutable AST gate enforces the deeper property).

The ``MiscRoute`` / ``ChatRoute`` fixtures and the in-process ASGI transport
(``_Transport`` / ``_drive_http`` / ``_is_not_implemented``) are REUSED from
``tests/edge/test_lgw15_app.py`` rather than reinvented.
"""

from __future__ import annotations

from collections.abc import Mapping

from gateway_v2.edge.openai_misc import EmbeddingsSurface, ModerationsSurface
from gateway_v2.edge.openai_misc_models import ModelsSurface
from gateway_v2.edge.routes import ChatRoute, build_routes
from tests.edge.test_lgw15_app import (
    _boot_chatroute,
    _drive_http,
    _is_not_implemented,
    _misc_route,
    _stub_provider,
)

#: The three misc EXACT keys GW16 wires to the real surface handlers (R1.1), paired with the
#: surface class the bound handler's ``__self__`` must be an instance of.
_MISC_EXACT: tuple[tuple[tuple[str, str], type], ...] = (
    (("POST", "/v1/embeddings"), EmbeddingsSurface),
    (("GET", "/v1/models"), ModelsSurface),
    (("POST", "/v1/moderations"), ModerationsSurface),
)

#: The single prefix key GW16 binds to ``ModelsSurface.handle_retrieve`` (R1.2, Decision 2).
_MODELS_PREFIX_KEY: tuple[str, str] = ("GET", "/v1/models/")

#: The three surfaces that stay declared ``not_implemented`` placeholders (R1.3). The three misc
#: keys are gone from this set (GW16), and so is ``/v1/responses`` (GW15 close-out).
_PLACEHOLDER_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/mcp"),
    ("POST", "/v1/rag/query"),
    ("POST", "/v1/vector/query"),
)


def _routes() -> tuple[
    Mapping[tuple[str, str], object], Mapping[tuple[str, str], object]
]:
    """Build the two route tables over a boot ``ChatRoute`` + a fail-closed ``MiscRoute``.

    Reuses the ``tests/edge/test_lgw15_app.py`` harness: ``_boot_chatroute`` recovers the real
    boot-built :class:`ChatRoute`, ``_misc_route`` builds a fail-closed :class:`MiscRoute` with
    the injected :class:`StubProviderClient` + the required renderer / catalog / clock. Both
    tables come straight out of ``build_routes`` so the test observes exactly what boot mounts.
    """
    provider = _stub_provider()
    chat = _boot_chatroute(provider)
    misc = _misc_route(provider)
    exact, prefix = build_routes(chat, misc)
    return exact, prefix


# --------------------------------------------------------------------------- #
# R1.1 — the three misc exact keys bind the REAL handlers, not the placeholder.
# --------------------------------------------------------------------------- #


def test_misc_exact_keys_bind_real_handlers_not_placeholder() -> None:
    """The three misc EXACT keys bind the real GW16 surface handlers, not ``_placeholder`` (R1.1).

    ``build_routes`` binds each as a bound method ``Surface(deps=misc).handle(...)``, so the
    handler carries a ``__self__`` that is an instance of the expected surface class. The
    ``_placeholder`` handler is a plain closure with NO ``__self__`` — asserting both the presence
    of ``__self__`` and its type proves the key is wired to the real handler and discriminates it
    from the not-implemented placeholder precisely.
    """
    exact, _prefix = _routes()
    for key, surface_cls in _MISC_EXACT:
        assert key in exact, f"misc route {key!r} is not registered in the exact table"
        handler = exact[key]
        bound_self = getattr(handler, "__self__", None)
        assert bound_self is not None, (
            f"misc route {key!r} is a plain closure (the _placeholder shape), not a real "
            f"{surface_cls.__name__}.handle bound method"
        )
        assert isinstance(bound_self, surface_cls), (
            f"misc route {key!r} is bound to {type(bound_self).__name__}, "
            f"not {surface_cls.__name__}"
        )


def test_misc_exact_keys_do_not_render_not_implemented() -> None:
    """Driving each misc exact handler does NOT yield the ``501 not_implemented`` envelope (R1.1).

    A behavioural cross-check of the structural assertion above: the ``_placeholder`` handler
    renders the declared ``501 not_implemented`` when driven; the real surfaces run their
    fail-closed lifecycle instead (a fail-closed identity ⇒ ``503 shared_state_unavailable``, a
    model-less / ``{}`` body ⇒ a declared malformed/missing code). Any outcome other than the
    ``not_implemented`` 501 proves the key is wired to the real handler.
    """
    provider = _stub_provider()

    def _app() -> object:
        from tests.edge.test_lgw15_app import _build_app

        return _build_app(provider)

    for (method, path), _surface_cls in _MISC_EXACT:
        transport = _drive_http(_app(), method, path)
        assert not _is_not_implemented(transport), (
            f"misc route {(method, path)!r} rendered the not_implemented placeholder — "
            "it is not wired to the real GW16 handler"
        )


# --------------------------------------------------------------------------- #
# R1.2 — the prefix table binds exactly ("GET","/v1/models/") -> retrieve.
# --------------------------------------------------------------------------- #


def test_prefix_table_binds_models_retrieve() -> None:
    """The ``prefix`` table binds exactly ``("GET", "/v1/models/")`` to ``handle_retrieve`` (R1.2).

    The retrieve path (``/v1/models/{id}``) cannot be a static exact key, so GW16 (Decision 2)
    puts it in a SECOND function-local table. The bound handler's ``__self__`` is a
    :class:`ModelsSurface` and its ``__func__`` is ``ModelsSurface.handle_retrieve`` (NOT
    ``handle_list``). Besides this key the prefix table holds only the two GW15
    ``/v1/responses/{id}`` id routes.
    """
    _exact, prefix = _routes()
    expected = {_MODELS_PREFIX_KEY, ("GET", "/v1/responses/"), ("DELETE", "/v1/responses/")}
    assert set(prefix) == expected, (
        f"prefix table keys are {set(prefix)!r}, expected exactly {expected!r}"
    )
    handler = prefix[_MODELS_PREFIX_KEY]
    bound_self = getattr(handler, "__self__", None)
    assert isinstance(bound_self, ModelsSurface), (
        "the models prefix route is not a ModelsSurface bound method"
    )
    assert getattr(handler, "__func__", None) is ModelsSurface.handle_retrieve, (
        "the models prefix route is not bound to ModelsSurface.handle_retrieve "
        "(wired to the wrong method)"
    )


# --------------------------------------------------------------------------- #
# R1.3 — the three remaining placeholders stay declared not_implemented.
# --------------------------------------------------------------------------- #


def test_remaining_placeholders_stay_not_implemented() -> None:
    """mcp / rag / vector still resolve to the declared ``501 not_implemented`` (R1.3).

    These three surfaces are registered-for-later: driving each over the in-process ASGI transport
    renders the ``not_implemented`` ``Error_Envelope`` (HTTP 501), never a raw 404-with-detail that
    would leak which surfaces exist.
    """
    from tests.edge.test_lgw15_app import _build_app

    for method, path in _PLACEHOLDER_ROUTES:
        transport = _drive_http(_build_app(_stub_provider()), method, path)
        assert transport.status() == 501, (
            f"placeholder {(method, path)!r} did not render 501: {transport.status()}"
        )
        assert transport.status() != 404, (
            f"placeholder {(method, path)!r} rendered a raw 404, not the declared 501"
        )
        assert _is_not_implemented(transport), (
            f"placeholder {(method, path)!r} is not the not_implemented Error_Envelope"
        )


def test_misc_keys_absent_from_placeholder_set() -> None:
    """The three misc exact keys are NOT placeholders — their closures carry no ``__self__`` (R1.3).

    The complement of R1.1/R1.3: none of the three misc exact handlers is a ``_placeholder``
    closure. A ``_placeholder`` handler has no ``__self__``; the real surface bound methods all do,
    so the misc keys and the three remaining placeholders are disjoint by handler shape.
    """
    exact, _prefix = _routes()
    for key, _surface_cls in _MISC_EXACT:
        assert key not in _PLACEHOLDER_ROUTES, f"{key!r} is wrongly in the placeholder set"
        assert getattr(exact[key], "__self__", None) is not None, (
            f"misc route {key!r} still has the _placeholder (closure, no __self__) shape"
        )


# --------------------------------------------------------------------------- #
# R1.4 (structural) — build_routes returns a 2-tuple of (function-local) Mappings.
# --------------------------------------------------------------------------- #


def test_build_routes_returns_two_mappings() -> None:
    """``build_routes`` returns a 2-tuple of ``Mapping`` route tables (both function-local).

    A structural assertion that both tables are plain ``Mapping`` values returned by
    ``build_routes`` (not module-level globals); the no-module-mutable AST gate enforces the deeper
    "built as a function local" property. Two independent builds also return DISTINCT table
    objects — a module-level shared table would alias the same object across calls.
    """
    exact, prefix = _routes()
    assert isinstance(exact, Mapping), "the exact route table is not a Mapping"
    assert isinstance(prefix, Mapping), "the prefix route table is not a Mapping"

    exact_2, prefix_2 = _routes()
    assert exact is not exact_2, "the exact table is a shared module-level object (aliased)"
    assert prefix is not prefix_2, "the prefix table is a shared module-level object (aliased)"


def test_chatroute_recovered_from_boot_is_a_chatroute() -> None:
    """Sanity: the harness recovers a real :class:`ChatRoute` the misc wiring builds alongside."""
    chat = _boot_chatroute(_stub_provider())
    assert isinstance(chat, ChatRoute), "the boot harness did not recover a ChatRoute"

# Feature: chat-surface-gw16-misc
# Validates: Requirements 6.4, 9.3, 9.4, 11.1, 11.2, 11.3
"""GW16 in-process OpenAI-SDK conformance subset against the v2 misc surfaces (task 10.1).

The GW16 sibling of ``test_gw15_chat_surface_v2.py``: the UNMODIFIED stock ``openai`` Python
SDK is pointed at the v2 misc surfaces (``POST /v1/embeddings``, ``GET /v1/models`` +
``GET /v1/models/{id}``, ``POST /v1/moderations``) with **only ``base_url`` and the API key
swapped**, driven over an in-process ``httpx.ASGITransport`` (no socket, no HTTP server)
against the genuine ASGI app assembled by :func:`gateway_v2.edge.app.build_app`, with the
in-process counting :class:`_CountingProvider` behind it as the provider substitute and a
scripted :class:`_ScriptedCatalog` behind the new ``build_app(catalog=...)`` seam.

It covers the misc **S-scenarios** the GW16 §Testing-Strategy conformance subset names, for
the stock SDK's ``client.embeddings`` / ``client.models`` / ``client.moderations`` surfaces,
and it structurally enforces the **LGW15-2 stop rule** (R9.3 / R9.4): the SDK is constructed
exactly once, by :func:`_stock_client`, with no monkeypatch, no custom transport logic, and no
request/response shimming — only ``base_url`` + ``api_key`` + the in-process ``http_client``
differ from a stock call against ``api.openai.com``. Any client-side code change needed to make
a scenario pass would have to live in that one helper and would be a visible Frozen_Contract
break.

**Scope — DEFERRED live gates kept as named seams, NOT scaffolded (R11.3).** The real embedding
provider socket, the real org/plan-derived model catalog backend, and the real-TCP / Node /
live-SDK run stay deferred live gates: this file stands up none of them (no listening socket, no
node runner, no real catalog backend). The ``_CountingProvider`` is the in-process substitute
for the real HTTP/SSE provider socket and ``_ScriptedCatalog`` is the in-process substitute for
the real catalog backend, both injected through ``build_app`` exactly as their real counterparts
would be, so each deferred seam is a drop-in behind the same surface, never an un-runnable gate.

**Misc S-scenarios, mapped to the GW16 behaviour under test**

* **Embeddings single input** — ``input="one string"`` → one ``data`` entry, ``object=="list"``,
  entry ``object=="embedding"``, ``index==0``; exactly ONE provider dispatch.
* **Embeddings batch input** — ``input=["a","b","c"]`` → three ``data`` entries, ``index`` 0,1,2
  in request order; ONE provider dispatch.
* **Embeddings input BLOCK** — an input-channel finding resolves to BLOCK (C22). ZERO provider
  dispatch; the stock SDK raises an ``openai.APIStatusError`` carrying the ``input_blocked``
  envelope (mirror of GW15 S02).
* **Models list** — ``client.models.list()`` → ``object=="list"`` carrying the catalog ids (the
  scripted :class:`_ScriptedCatalog` via the ``build_app(catalog=...)`` seam).
* **Models retrieve known** — ``client.models.retrieve("<known-id>")`` → ``object=="model"``,
  id matches.
* **Models retrieve unknown** — ``client.models.retrieve("<absent-id>")`` → ``openai.NotFoundError``
  (HTTP 404, code ``model_not_found``).
* **Moderations benign** — ``client.moderations.create(input="hello")`` → ``results[0].flagged``
  is ``False``.
* **Moderations flagged** — an input whose detector produces an EXECUTED finding →
  ``results[0].flagged`` is ``True`` with the mapped category set.
* **Moderations missing input** — a raw ``httpx`` POST with an empty body (the stock SDK cannot
  send a ``/v1/moderations`` request with no ``input``) → ``400 invalid_request_error`` /
  ``missing_required_parameter``. Kept clearly separated from the stock-SDK scenarios.

**House wiring.** The lifecycle deps that must PASS for an embeddings request to reach the
provider (resolving identity, a warmed plan snapshot, always-admit admission + budget) come from
the shared :mod:`tests.edge._chat_lifecycle_fixtures`; the moderations + models surfaces only
need the resolving identity (they run no admission/budget/plan phase). Each scenario swaps ONLY
the per-scenario ``input_detector`` / ``catalog`` levers. ``asyncio`` is driven through the stock
``openai.AsyncOpenAI`` client; the whole file ``pytest.importorskip``s ``openai`` + ``httpx`` so
a tree without the SDK installed skips cleanly rather than erroring the collection.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence

import pytest

# The subset drives the UNMODIFIED stock SDK. A tree without it skips cleanly (the real-TCP /
# Node runs are deferred gates; this in-process subset needs the Python SDK + httpx present).
openai = pytest.importorskip("openai")
httpx = pytest.importorskip("httpx")

from gateway_v2.detect.holdback import scan as holdback_scan  # noqa: E402
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest  # noqa: E402
from gateway_v2.domain import Category, Finding, FindingStatus, Span  # noqa: E402
from gateway_v2.edge.app import CatalogModel, build_app  # noqa: E402
from gateway_v2.edge.identity_resolver import TenantIdentity  # noqa: E402
from gateway_v2.egress.output_guard import MinimalOutputResolver  # noqa: E402
from gateway_v2.runtime.kinds import HardwareSignals  # noqa: E402
from gateway_v2.runtime.resources import ResourceContract, from_signals  # noqa: E402
from tests.edge._chat_lifecycle_fixtures import (  # noqa: E402
    AdmittingAdmission,
    AdmittingBudget,
    ResolvingIdentity,
    warm_snapshot_source,
)

pytestmark = pytest.mark.asyncio

_BASE_URL = "http://misc-surface.test/v1"
_API_KEY = "zs-gw16-conformance-key"
_MODEL = "text-embedding-3-small"

#: A canary a BLOCK/flag scenario places so a scenario can key its finding to it unambiguously.
_CANARY = "ZS-CANARY-4242-SECRET"


# --------------------------------------------------------------------------- #
# App assembly: the genuine build_app output with passing lifecycle deps.
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    """A serviceable contract (the shared ``from_signals`` fixture shape the edge tests use)."""
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
    """A minimal metrics registry: a fixed text exposition (the misc path never reads it)."""

    __slots__ = ()

    def exposition(self) -> str:
        return "# gateway_v2 metrics\n"


class _Clock:
    """A monotonic in-process clock (seconds), advanced on each read, never real."""

    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


def _no_findings(_text: str) -> Sequence[Finding]:
    """A detector that finds nothing (the benign baseline)."""
    return ()


class _CountingProvider:
    """A :class:`~gateway_v2.dispatch.provider.ProviderClient` that counts every ``open``.

    Replays a scripted sequence of :class:`UpstreamEvent` (socket-free, deterministic) and
    tallies ``open`` so a scenario can assert the provider-dispatch count (exactly one for an
    embeddings completion, zero for an input BLOCK). This is the in-process substitute for the
    real HTTP/SSE provider socket (the deferred named seam) — it satisfies the shipped
    ``ProviderClient`` protocol structurally (``open`` returns an async iterator, ``abort`` is a
    coroutine), so the real client drops in behind the same surface.

    GW16 embeddings vectors ride the one transport the shipped ``UpstreamEvent`` offers: each
    ``text_deltas`` pair's TEXT is a JSON-encoded array of floats = one embedding vector, and the
    arrival order IS the batch index order (the ``edge/openai_misc.py`` drain convention).
    """

    __slots__ = ("abort_calls", "open_calls", "script")

    def __init__(self, script: Sequence[UpstreamEvent]) -> None:
        self.script: tuple[UpstreamEvent, ...] = tuple(script)
        self.open_calls = 0
        self.abort_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        del request
        return self._replay()

    async def _replay(self) -> AsyncIterator[UpstreamEvent]:
        for event in self.script:
            yield event

    async def abort(self) -> None:
        self.abort_calls += 1


def _vector_script(vectors: Sequence[tuple[float, ...]]) -> tuple[UpstreamEvent, ...]:
    """Script one :class:`UpstreamEvent` per embedding vector, in batch-index order.

    Each event carries a single ``("embeddings_input", <json float array>)`` text-delta pair; the
    surface decodes the JSON array to a ``tuple[float, ...]`` and assigns the sequential index, so
    the arrival order IS the batch index order (R6.3). The last event carries ``final=True``.
    """
    last = len(vectors) - 1
    return tuple(
        UpstreamEvent(
            text_deltas=(("embeddings_input", json.dumps(list(vector))),),
            final=(i == last),
        )
        for i, vector in enumerate(vectors)
    )


class _ScriptedCatalog:
    """An in-process :class:`~gateway_v2.edge.app.CatalogSource` returning a fixed catalog.

    The in-process substitute for the real org/plan-derived model-catalog backend (the deferred
    named seam, R11.3), injected through the new ``build_app(catalog=...)`` seam. It returns the
    SAME scripted tuple of :class:`CatalogModel` for every identity, so ``GET /v1/models`` lists
    them and ``GET /v1/models/{id}`` retrieves a present id (else ``model_not_found``). It
    satisfies the shipped ``CatalogSource`` protocol structurally (``catalog_for`` returns a
    ``tuple[CatalogModel, ...]``), so the real backend drops in behind the same surface.
    """

    __slots__ = ("_models",)

    def __init__(self, models: Sequence[CatalogModel]) -> None:
        self._models: tuple[CatalogModel, ...] = tuple(models)

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]:
        del identity
        return self._models


#: A small scripted catalog for the models scenarios (two known ids, catalog order preserved).
_KNOWN_MODEL_ID = "zs-model-alpha"
_SECOND_MODEL_ID = "zs-model-beta"
_ABSENT_MODEL_ID = "zs-model-does-not-exist"
_SCRIPTED_MODELS = (
    CatalogModel(id=_KNOWN_MODEL_ID, created=1_700_000_000, owned_by="zeroshield"),
    CatalogModel(id=_SECOND_MODEL_ID, created=1_700_000_100, owned_by="zeroshield"),
)


def _finding(detector: str, category: Category, text: str) -> Finding:
    """A whole-text EXECUTED finding naming ``detector`` + ``category`` over ``text``.

    ``detector`` drives the C22 disposition (``card`` → BLOCK via the MinimalOutputResolver);
    ``category`` drives the moderation category mapping (``Category.PII`` → the ``pii`` moderation
    category). An EXECUTED finding carries ``confidence`` per the ``Finding`` contract.
    """
    return Finding(
        detector=detector,
        detector_version="gw16-conformance",
        category=category,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(start=0, end=len(text)),),
        evidence=None,
    )


def _build_app(
    *,
    provider: _CountingProvider,
    input_detector: Callable[[str], Sequence[Finding]] | None = None,
    catalog: _ScriptedCatalog | None = None,
) -> object:
    """Assemble the genuine ASGI app with PASSING lifecycle deps + the scenario levers.

    The identity / plan-snapshot / admission / budget deps all PASS (so a well-formed embeddings
    request reaches the provider; moderations + models need only the resolving identity). The
    per-scenario ``input_detector`` (input scan) and ``catalog`` (the new ``build_app(catalog=...)``
    seam) are the only levers that change between scenarios. The shared ``MinimalOutputResolver``
    resolves the input scan (C22 ``most_restrictive``).
    """
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=_StubMetrics(),
        clock=_Clock(),
        input_detector=input_detector if input_detector is not None else _no_findings,
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=AdmittingAdmission(),
        quota=AdmittingBudget(),
        catalog=catalog,
    )


def _stock_client(app: object) -> openai.AsyncOpenAI:
    """The UNMODIFIED stock ``openai.AsyncOpenAI`` — only ``base_url`` + key swapped (LGW15-2).

    This is the ONE place the SDK is constructed. It differs from a stock call against
    ``api.openai.com`` by exactly: (1) ``base_url`` points at the in-process gateway, (2) the API
    key is the test key, and (3) the ``http_client`` uses an in-process ``ASGITransport`` instead
    of a real socket — the transport is httpx's own, not a custom shim. No request/response code
    is overridden. If a scenario needed any OTHER client-side change to pass, it would have to be
    added here and would be a visible break of the Frozen_Contract (the R9.3/R9.4 stop rule).
    """
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://misc-surface.test")
    return openai.AsyncOpenAI(
        base_url=_BASE_URL,
        api_key=_API_KEY,
        http_client=http_client,
        max_retries=0,
    )


# =========================================================================== #
# Embeddings — single input: one data entry, one provider dispatch.
# =========================================================================== #


async def test_embeddings_single_input() -> None:
    """Embeddings single string input → one ``data`` entry at ``index==0``, ONE dispatch."""
    provider = _CountingProvider(_vector_script([(0.1, 0.2, 0.3)]))
    client = _stock_client(_build_app(provider=provider))
    try:
        response = await client.embeddings.create(model=_MODEL, input="one string")
    finally:
        await client.close()

    assert response.object == "list"
    assert len(response.data) == 1
    entry = response.data[0]
    assert entry.object == "embedding"
    assert entry.index == 0
    assert tuple(entry.embedding) == (0.1, 0.2, 0.3)
    assert provider.open_calls == 1, "a single embeddings input must make one provider dispatch"


# =========================================================================== #
# Embeddings — batch input: three data entries, index order preserved, one dispatch.
# =========================================================================== #


async def test_embeddings_batch_input_index_order() -> None:
    """Embeddings batch input → three ``data`` entries, ``index`` 0,1,2 in request order."""
    vectors = [(1.0,), (2.0,), (3.0,)]
    provider = _CountingProvider(_vector_script(vectors))
    client = _stock_client(_build_app(provider=provider))
    try:
        response = await client.embeddings.create(model=_MODEL, input=["a", "b", "c"])
    finally:
        await client.close()

    assert response.object == "list"
    assert [entry.index for entry in response.data] == [0, 1, 2]
    assert [tuple(entry.embedding) for entry in response.data] == [(1.0,), (2.0,), (3.0,)]
    assert all(entry.object == "embedding" for entry in response.data)
    assert provider.open_calls == 1, "a batch embeddings input must make one provider dispatch"


# =========================================================================== #
# Embeddings — input BLOCK: zero provider dispatch, SDK gets a documented error.
# =========================================================================== #


def _block_input_detector(text: str) -> Sequence[Finding]:
    """An input detector whose ``card`` finding resolves to BLOCK (C22 terminate class)."""
    return (_finding("card", Category.PII, text),)


async def test_embeddings_input_block_zero_dispatch() -> None:
    """An embeddings input BLOCK → ZERO provider dispatch, a documented ``input_blocked`` error."""
    provider = _CountingProvider(_vector_script([(0.1,)]))
    client = _stock_client(_build_app(provider=provider, input_detector=_block_input_detector))
    try:
        with pytest.raises(openai.APIError) as excinfo:
            await client.embeddings.create(model=_MODEL, input=f"leak {_CANARY}")
    finally:
        await client.close()

    err = excinfo.value
    assert isinstance(err, openai.APIStatusError), f"expected an APIStatusError, got {err!r}"
    body = json.loads(err.response.text)
    assert isinstance(body.get("error"), dict), f"not an Error_Envelope body: {body!r}"
    assert body["error"].get("code") == "input_blocked", f"unexpected error body: {body!r}"
    assert provider.open_calls == 0, "an input BLOCK must make ZERO provider dispatch"


# =========================================================================== #
# Models — list: the scripted catalog ids, object == "list".
# =========================================================================== #


async def test_models_list_returns_catalog() -> None:
    """``client.models.list()`` → ``object=="list"`` carrying the scripted catalog ids in order."""
    provider = _CountingProvider(())
    catalog = _ScriptedCatalog(_SCRIPTED_MODELS)
    client = _stock_client(_build_app(provider=provider, catalog=catalog))
    try:
        page = await client.models.list()
        models = [model async for model in page]
    finally:
        await client.close()

    assert [model.id for model in models] == [_KNOWN_MODEL_ID, _SECOND_MODEL_ID]
    assert all(model.object == "model" for model in models)
    assert provider.open_calls == 0, "a models list must make ZERO provider dispatch"


# =========================================================================== #
# Models — retrieve known: object == "model", id matches.
# =========================================================================== #


async def test_models_retrieve_known() -> None:
    """``client.models.retrieve(<known>)`` → ``object=="model"`` with the matching id."""
    provider = _CountingProvider(())
    catalog = _ScriptedCatalog(_SCRIPTED_MODELS)
    client = _stock_client(_build_app(provider=provider, catalog=catalog))
    try:
        model = await client.models.retrieve(_KNOWN_MODEL_ID)
    finally:
        await client.close()

    assert model.object == "model"
    assert model.id == _KNOWN_MODEL_ID
    assert provider.open_calls == 0, "a models retrieve must make ZERO provider dispatch"


# =========================================================================== #
# Models — retrieve unknown: openai.NotFoundError (HTTP 404, model_not_found).
# =========================================================================== #


async def test_models_retrieve_unknown_raises_not_found() -> None:
    """``client.models.retrieve(<absent>)`` → ``openai.NotFoundError`` (404 model_not_found)."""
    provider = _CountingProvider(())
    catalog = _ScriptedCatalog(_SCRIPTED_MODELS)
    client = _stock_client(_build_app(provider=provider, catalog=catalog))
    try:
        with pytest.raises(openai.NotFoundError) as excinfo:
            await client.models.retrieve(_ABSENT_MODEL_ID)
    finally:
        await client.close()

    err = excinfo.value
    assert err.status_code == 404, f"expected HTTP 404, got {err.status_code}"
    body = json.loads(err.response.text)
    assert body["error"].get("code") == "model_not_found", f"unexpected error body: {body!r}"
    assert provider.open_calls == 0, "an unknown model retrieve must make ZERO provider dispatch"


# =========================================================================== #
# Moderations — benign: results[0].flagged == False.
# =========================================================================== #


async def test_moderations_benign_not_flagged() -> None:
    """``client.moderations.create(input="hello")`` with no finding → ``flagged`` is ``False``."""
    provider = _CountingProvider(())
    client = _stock_client(_build_app(provider=provider))
    try:
        response = await client.moderations.create(input="hello")
    finally:
        await client.close()

    assert response.results[0].flagged is False
    assert provider.open_calls == 0, "moderations must make ZERO provider dispatch"


# =========================================================================== #
# Moderations — flagged: an EXECUTED finding sets flagged True with the mapped category.
# =========================================================================== #


def _flag_moderation_detector(text: str) -> Sequence[Finding]:
    """An EXECUTED ``Category.PII`` finding detector → the ``pii`` moderation category."""
    return (_finding("pii", Category.PII, text),)


async def test_moderations_flagged_sets_category() -> None:
    """A moderations input with an EXECUTED finding → ``flagged`` True + the mapped category."""
    provider = _CountingProvider(())
    client = _stock_client(
        _build_app(provider=provider, input_detector=_flag_moderation_detector)
    )
    try:
        response = await client.moderations.create(input=f"contains {_CANARY}")
    finally:
        await client.close()

    result = response.results[0]
    assert result.flagged is True, "an EXECUTED finding must flag the moderation result"
    # The firewall ``Category.PII`` rides as the ``pii`` moderation category (a firewall EXTRA);
    # the SDK surfaces unknown categories on the raw model dump, so assert on the JSON body.
    categories = response.model_dump()["results"][0]["categories"]
    assert categories.get("pii") is True, f"the mapped category is absent: {categories!r}"
    assert provider.open_calls == 0, "moderations must make ZERO provider dispatch"


# =========================================================================== #
# Moderations — missing input: 400 invalid_request_error / missing_required_parameter.
#
# The stock SDK cannot send a /v1/moderations request with no ``input`` (``input`` is a required
# argument of ``moderations.create``), so this one case is driven with a RAW httpx POST of an
# empty JSON body, asserting the declared 400 envelope. It is kept clearly separated from the
# stock-SDK scenarios above and does NOT construct the openai client.
# =========================================================================== #


async def test_moderations_missing_input_raw_post() -> None:
    """A ``/v1/moderations`` body with no ``input`` → 400 invalid_request_error (raw httpx POST)."""
    provider = _CountingProvider(())
    app = _build_app(provider=provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://misc-surface.test"
    ) as raw:
        response = await raw.post(
            "/v1/moderations",
            content=b"{}",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400, f"expected HTTP 400, got {response.status_code}"
    body = response.json()
    assert body["error"].get("type") == "invalid_request_error", f"unexpected body: {body!r}"
    assert body["error"].get("code") == "missing_required_parameter", f"unexpected body: {body!r}"
    assert provider.open_calls == 0, "a malformed moderations body must make ZERO provider dispatch"


# =========================================================================== #
# LGW15-2 — the base-URL swap acceptance: an unmodified SDK works unchanged.
# =========================================================================== #


async def test_base_url_swap_only_embeddings() -> None:
    """The unmodified SDK, base_url + key swapped only, drives embeddings (R9.3/R9.4).

    The ``_stock_client`` helper is the ONLY client construction and swaps exactly ``base_url`` +
    the key (plus the in-process httpx transport) — no request/response code is overridden. A
    clean embeddings completion through it proves the Frozen_Contract holds with zero client code
    change for the misc surfaces.
    """
    provider = _CountingProvider(_vector_script([(0.5, 0.6)]))
    client = _stock_client(_build_app(provider=provider))
    try:
        response = await client.embeddings.create(
            model=_MODEL, input="unmodified SDK, base-url swap only"
        )
    finally:
        await client.close()

    assert response.object == "list"
    assert tuple(response.data[0].embedding) == (0.5, 0.6)

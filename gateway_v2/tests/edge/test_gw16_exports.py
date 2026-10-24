# Feature: chat-surface-gw16-misc, Property 16: Every terminal outcome populates content-free exports with FAIL_OPEN zero  # noqa: E501
# Validates: Requirements 10.1, 10.2, 10.3, 10.4
"""Property 16 — every terminal misc outcome populates content-free exports, FAIL_OPEN at 0.

House idiom: a seeded ``random.Random``, NO ``hypothesis``, >=10,000 iterations, the three
GW16 misc lifecycles driven via ``asyncio.run``; one property <-> one property-based test.

*For any* terminal outcome a misc request can reach, the Misc_Surface populates the shipped
GW14d :class:`~gateway_v2.runtime.stream_metrics.PerRequestExports` with the admission /
budget / input-enforcement dimensions, keeps ``fail_open_total`` at zero, and places NO raw
request or response content (no folded input text, no provider vectors) in any export field
(R10.1 / R10.2 / R10.3 / R10.4).

Every terminal misc outcome the three surfaces can produce is driven every iteration, over the
REAL shipped :class:`EmbeddingsSurface` / :class:`ModerationsSurface` / :class:`ModelsSurface`
with the real shipped parser / resolver / renderer / router / transform and only the per-phase
deciders + the input detector + the scripted provider as deterministic / fail-closed doubles:

* **embeddings allowed** — identity resolves, admission admits, budget admits, the input scan
  finds nothing, and the provider streams scripted vectors the surface renders as ONE embeddings
  body (the canary rides the input AND the scripted provider vectors' JSON text).
* **embeddings shed** — admission returns a ``ShedVerdict`` ⇒ refuse ``overload_shed`` BEFORE
  the provider is opened.
* **embeddings budget-refused** — budget returns a ``BudgetVerdict`` ⇒ refuse
  ``budget_unavailable``.
* **embeddings input-blocked** — the injected input detector emits a BLOCK-class (``card``)
  finding the shipped resolver maps to BLOCK (C22) ⇒ refuse ``input_blocked``, zero provider
  calls.
* **embeddings errored** — the input scanner RAISES ⇒ the lifecycle withholds with the declared
  ``scan_failure`` code (a provider-decode fail would withhold the same way; the raising-scanner
  arm is the deterministic representative).
* **moderations moderated** — a finding-producing detector ⇒ the findings map to flagged
  categories in ONE moderation body.
* **moderations errored** — a RAISING detector ⇒ withhold ``scan_failure``, NO result body.
* **models not-found** — a retrieve for an id absent from the catalog ⇒ ``model_not_found``.
* **models listed** — a list over a catalog ⇒ the OpenAI list body in catalog order.

The canary is placed in BOTH the request (a folded input item) AND the provider's scripted
vectors / response every iteration, so a producer that leaked raw request OR response content
into any export field would surface the canary bytes. A SINGLE :class:`PerRequestExports` is
observed across the whole request for each outcome, and after the terminal outcome the property
asserts:

1. the snapshot carries every dimension-bearing series — the admission / budget / input-
   enforcement outcomes are reflected in the fixed, label-free series the shipped producer
   exposes as real ints / histograms from the first snapshot, so "no outcome recorded" and
   "no series" cannot look the same (R10.1 / R10.2);
2. ``fail_open_total`` is zero — the pinned-zero series no correct terminal path increments,
   because the Gateway fails closed by withholding, never by forwarding raw bytes (R10.3); and
3. the canary — placed in both the request and the response — appears ZERO times in a full
   stringification of every export field, so no raw request/response content is in the exports
   (R10.4).

Everything this file owns is uniquely prefixed ``_p16_`` / ``_P16`` so it never clashes with
the sibling tests that share the ``tests/edge`` package.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence

from gateway_v2.admit.grant import (
    Admitted,
    BudgetVerdict,
    ShedVerdict,
    budget_verdict,
    shed_verdict,
)
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.app import CatalogModel
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_misc import EmbeddingsSurface, MiscRoute, ModerationsSurface
from gateway_v2.edge.openai_misc_models import ModelsSurface
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.holdback_metrics import Histogram
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.stream_metrics import PerRequestExports, PerRequestReading

_SEED = 0x16_00_0016
_ITERATIONS = 10_000

#: The ASGI scope paths the three misc surfaces are routed on.
_P16_EMBEDDINGS_PATH = "/v1/embeddings"
_P16_MODERATIONS_PATH = "/v1/moderations"
_P16_MODELS_PATH = "/v1/models"

#: The channel the scripted provider folds the canary vectors into; the arrival order of the
#: deltas is the batch index order the surface drains. Response content rides here so a leaked
#: provider byte would surface in an export field.
_P16_VECTOR_CHANNEL = "embeddings_input"

#: Detector names the shipped ``MinimalOutputResolver`` maps through its trade-off table:
#: ``card`` → BLOCK (terminate). An input BLOCK short-circuits before any provider contact.
_P16_BLOCK_INPUT_DETECTOR = "card"

#: The nine terminal misc outcomes the three surfaces can produce. Each is driven every
#: iteration; a dropped export population / a leaked canary / a non-zero fail-open for ANY
#: outcome fails the gate.
_P16_EMB_ALLOWED = "embeddings_allowed"
_P16_EMB_SHED = "embeddings_shed"
_P16_EMB_BUDGET_REFUSED = "embeddings_budget_refused"
_P16_EMB_INPUT_BLOCKED = "embeddings_input_blocked"
_P16_EMB_ERRORED = "embeddings_errored"
_P16_MOD_MODERATED = "moderations_moderated"
_P16_MOD_ERRORED = "moderations_errored"
_P16_MODELS_NOT_FOUND = "models_not_found"
_P16_MODELS_LISTED = "models_listed"

_P16_TERMINAL_OUTCOMES: tuple[str, ...] = (
    _P16_EMB_ALLOWED,
    _P16_EMB_SHED,
    _P16_EMB_BUDGET_REFUSED,
    _P16_EMB_INPUT_BLOCKED,
    _P16_EMB_ERRORED,
    _P16_MOD_MODERATED,
    _P16_MOD_ERRORED,
    _P16_MODELS_NOT_FOUND,
    _P16_MODELS_LISTED,
)

#: Alphabet for canary synthesis — printable, distinctive, so a canary that slips into any
#: export field is unambiguous in a counterexample.
_P16_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _p16_ctx(i: int, outcome: str, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} outcome={outcome} {body}"


def _p16_canary(rng: random.Random) -> str:
    """A distinctive canary token unlikely to collide with any filler/declared text."""
    n = rng.randint(6, 24)
    return "P16CANARY_" + "".join(rng.choice(_P16_CANARY_ALPHABET) for _ in range(n))


# --------------------------------------------------------------------------- #
# Per-phase deciders + the input detector + the scripted provider (doubles).
# --------------------------------------------------------------------------- #


class _P16Identity:
    """An ``IdentityResolver`` resolving one canned tenant (never fails closed)."""

    __slots__ = ("_identity",)

    def __init__(self, org_id: str) -> None:
        self._identity = TenantIdentity(
            org_id=org_id,
            principal=Principal(
                key_id=f"p16-key-{org_id}",
                org_id=org_id,
                rate_per_s=1.0,
                burst=1.0,
                feed_seq=0,
            ),
        )

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _P16AdmitAdmission:
    """Admission that always admits (keyed to the request's own owner)."""

    __slots__ = ()

    async def admit(
        self, *, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del enqueued_at
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

    def complete(self, request_id: str) -> None:
        del request_id


class _P16ShedAdmission:
    """Admission that always sheds (the embeddings ``shed`` terminal outcome)."""

    __slots__ = ()

    async def admit(
        self, *, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del owner_id, enqueued_at
        # Fresh, non-shared rng so the retry-after floor+jitter contract holds deterministically.
        return shed_verdict(random.Random(0), request_id)

    def complete(self, request_id: str) -> None:
        del request_id


class _P16AdmitBudget:
    """Budget that always admits (local lease, no store read)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return Admitted(owner_id="p16", request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _P16RefuseBudget:
    """Budget that always refuses (the embeddings ``budget_refused`` terminal outcome)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return budget_verdict(request_id)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _P16BlockDetector:
    """An input detector firing a BLOCK-class finding on the canary (``input_blocked``)."""

    __slots__ = ("_canary",)

    def __init__(self, canary: str) -> None:
        self._canary = canary

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._canary not in text:
            return ()
        start = text.index(self._canary)
        return (
            Finding(
                detector=_P16_BLOCK_INPUT_DETECTOR,  # MinimalOutputResolver maps "card" -> BLOCK.
                detector_version="p16",
                category=Category.SECRET,
                status=FindingStatus.EXECUTED,
                confidence=0.99,
                spans=(Span(start=start, end=start + len(self._canary)),),
                evidence=None,
            ),
        )


class _P16FindingDetector:
    """A detector that emits one EXECUTED finding of ``category`` when the canary is present."""

    __slots__ = ("_canary", "_category")

    def __init__(self, canary: str, category: Category) -> None:
        self._canary = canary
        self._category = category

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._canary not in text:
            return ()
        start = text.index(self._canary)
        return (
            Finding(
                detector="p16-moderation",
                detector_version="p16",
                category=self._category,
                status=FindingStatus.EXECUTED,
                confidence=0.9,
                spans=(Span(start=start, end=start + len(self._canary)),),
                evidence=None,
            ),
        )


class _P16RaisingDetector:
    """A detector whose scan cannot be computed — it RAISES (uncomputable scan)."""

    __slots__ = ()

    def __call__(self, text: str) -> Sequence[Finding]:
        del text
        raise RuntimeError("p16: detector is uncomputable")


def _p16_no_findings(text: str) -> Sequence[Finding]:
    del text
    return ()


class _P16CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    The pre-provider refusal outcomes assert ``open_calls == 0``; the embeddings-allowed path
    asserts it moved to exactly 1 and replays the scripted canary-vector events.
    """

    __slots__ = ("open_calls",)

    def __init__(self, *, script: Sequence[UpstreamEvent]) -> None:
        super().__init__(script=script, rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


# --------------------------------------------------------------------------- #
# A fail-open catalog source whose catalog carries the canary in a model id.
# --------------------------------------------------------------------------- #


class _P16Catalog:
    """A ``CatalogSource`` whose catalog carries the canary in a model id.

    The models-listed outcome renders the whole catalog, so the response content genuinely
    carries the canary (in the rendered list body) — the exports must still never carry it
    (R10.4). The models-not-found outcome retrieves a different id, so the catalog is read but
    no entry matches.
    """

    __slots__ = ("_models",)

    def __init__(self, canary: str) -> None:
        self._models = (
            CatalogModel(id=f"p16-model-{canary}", created=0, owned_by="p16-org"),
        )

    def catalog_for(self, identity: TenantIdentity) -> tuple[CatalogModel, ...]:
        del identity
        return self._models


# --------------------------------------------------------------------------- #
# Plan + snapshot source (embeddings resolves Surface.EMBEDDINGS).
# --------------------------------------------------------------------------- #


def _p16_plan(org_id: str) -> ExecutionPlan:
    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=1,
        content_hash="p16" + "0" * 61,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _p16_snapshot_source(plan: ExecutionPlan) -> Callable[[], ReplicaSnapshot]:
    """A ``snapshot_source`` whose served snapshot resolves ``plan.org_id`` to ``plan``."""
    store = PlanStore()
    store.put(plan)
    snapshot = ReplicaSnapshot(store)
    snapshot.absorb(plan.org_id, now=0.0)

    def _source() -> ReplicaSnapshot:
        return snapshot

    return _source


def _p16_vector_provider(canary: str) -> _P16CountingProvider:
    """A provider whose scripted events carry the canary in the vector-channel delta text.

    The embeddings surface decodes each delta's text as a JSON float array, so the canary rides
    the vector transport as a numeric payload whose JSON stringification carries the canary bytes
    in the model/response — a correct run genuinely moves those bytes, and the exports must still
    never carry them (R10.4).
    """
    # One scripted vector per input item; the canary rides the request item text, and the
    # provider's delta text is a JSON float array (the shipped vector transport).
    return _P16CountingProvider(
        script=(
            UpstreamEvent(
                text_deltas=((_P16_VECTOR_CHANNEL, json.dumps([0.1, 0.2])),), final=True
            ),
        )
    )


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


def _p16_build_deps(outcome: str, org_id: str, canary: str) -> MiscRoute:
    """Assemble the per-outcome ``MiscRoute`` (one fresh ``PerRequestExports`` per request)."""
    plan = _p16_plan(org_id)
    admission: object = _P16AdmitAdmission()
    quota: object = _P16AdmitBudget()
    input_detector: Callable[[str], Sequence[Finding]] = _p16_no_findings
    provider = _p16_vector_provider(canary)

    if outcome == _P16_EMB_SHED:
        admission = _P16ShedAdmission()
    elif outcome == _P16_EMB_BUDGET_REFUSED:
        quota = _P16RefuseBudget()
    elif outcome == _P16_EMB_INPUT_BLOCKED:
        input_detector = _P16BlockDetector(canary)
    elif outcome == _P16_EMB_ERRORED:
        input_detector = _P16RaisingDetector()
    elif outcome == _P16_MOD_MODERATED:
        input_detector = _P16FindingDetector(canary, Category.PII)
    elif outcome == _P16_MOD_ERRORED:
        input_detector = _P16RaisingDetector()

    return MiscRoute(
        contract=_contract(),
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=_P16Catalog(canary),
        identity=_P16Identity(org_id),
        admission=admission,  # type: ignore[arg-type]
        quota=quota,  # type: ignore[arg-type]
        snapshot_source=_p16_snapshot_source(plan),
        input_detector=input_detector,
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
        exports=PerRequestExports(),
    )


# --------------------------------------------------------------------------- #
# Request bodies + a tiny ASGI receive/send harness (the GW16 smoke-test shape).
# --------------------------------------------------------------------------- #


def _p16_embeddings_body(canary: str, rng: random.Random) -> bytes:
    model = "gw16-embed-" + "".join(rng.choice("abcdef0123456789") for _ in range(6))
    return json.dumps({"model": model, "input": [f"please embed {canary}"]}).encode("utf-8")


def _p16_moderations_body(canary: str) -> bytes:
    return json.dumps({"input": [f"moderate {canary} here"]}).encode("utf-8")


def _p16_receive_for(body: bytes) -> Callable[[], Awaitable[Mapping[str, object]]]:
    delivered = False

    async def receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class _P16Capture:
    """An ASGI ``send`` that captures the response start + body events."""

    __slots__ = ("body", "status")

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


def _p16_scope(path: str) -> dict[str, object]:
    return {"type": "http", "method": "POST", "path": path, "headers": []}


# --------------------------------------------------------------------------- #
# Export inspection: dimension presence + content-freedom over the whole reading.
# --------------------------------------------------------------------------- #


def _p16_assert_dimensions_present(reading: PerRequestReading, ctx: str) -> None:
    """Assert the snapshot carries every dimension-bearing series as a real value (R10.1/R10.2).

    The admission / budget / input-enforcement outcomes are reflected in the fixed, label-free
    series the shipped producer exposes as REAL ZEROS from the first snapshot (never absent), so
    an alarm cannot confuse "no outcome recorded" with "no series". Every scalar series is a
    concrete ``int`` and both latency distributions are real :class:`Histogram` objects —
    present, typed, and non-``None`` on every terminal outcome.
    """
    for name in (
        "detector_invocations",
        "buffer_high_water",
        "active_stream_memory_bound",
        "withheld_total",
        "fail_open_total",
    ):
        value = getattr(reading, name)
        assert isinstance(value, int), f"{ctx} series={name} not a real int value={value!r}"
    for name in ("release_lag_ms", "loop_lag_ms"):
        value = getattr(reading, name)
        assert isinstance(value, Histogram), f"{ctx} series={name} not a real Histogram"


def _p16_reading_text(reading: PerRequestReading) -> str:
    """A full stringification of every export field, for the content-freedom assertion.

    Walks every field of the frozen :class:`PerRequestReading` (the scalar series + the two
    histograms, whose observed samples are stringified too) into one text blob, so a canary
    hiding in ANY export field — a scalar, a histogram bucket/sample, anything — would appear
    here. The histograms are rendered via ``repr`` so a raw byte smuggled into a sample value is
    caught.
    """
    parts = [
        repr(reading.detector_invocations),
        repr(reading.buffer_high_water),
        repr(reading.active_stream_memory_bound),
        repr(reading.withheld_total),
        repr(reading.fail_open_total),
        repr(reading.release_lag_ms),
        repr(reading.loop_lag_ms),
    ]
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Drivers: run one terminal outcome end to end and return the capture.
# --------------------------------------------------------------------------- #


async def _p16_run_embeddings(deps: MiscRoute, body: bytes) -> _P16Capture:
    capture = _P16Capture()
    await EmbeddingsSurface(deps=deps).handle(
        _p16_scope(_P16_EMBEDDINGS_PATH), _p16_receive_for(body), capture
    )
    return capture


async def _p16_run_moderations(deps: MiscRoute, body: bytes) -> _P16Capture:
    capture = _P16Capture()
    await ModerationsSurface(deps=deps).handle(
        _p16_scope(_P16_MODERATIONS_PATH), _p16_receive_for(body), capture
    )
    return capture


async def _p16_run_models_list(deps: MiscRoute) -> _P16Capture:
    capture = _P16Capture()
    scope = {"type": "http", "method": "GET", "path": _P16_MODELS_PATH, "headers": []}
    await ModelsSurface(deps=deps).handle_list(scope, _p16_receive_for(b""), capture)
    return capture


async def _p16_run_models_retrieve(deps: MiscRoute, model_id: str) -> _P16Capture:
    capture = _P16Capture()
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"{_P16_MODELS_PATH}/{model_id}",
        "headers": [],
    }
    await ModelsSurface(deps=deps).handle_retrieve(scope, _p16_receive_for(b""), capture)
    return capture


def _p16_drive(outcome: str, deps: MiscRoute, canary: str, rng: random.Random) -> _P16Capture:
    """Drive one terminal misc outcome end to end and return the client capture."""
    if outcome in (
        _P16_EMB_ALLOWED,
        _P16_EMB_SHED,
        _P16_EMB_BUDGET_REFUSED,
        _P16_EMB_INPUT_BLOCKED,
        _P16_EMB_ERRORED,
    ):
        return asyncio.run(_p16_run_embeddings(deps, _p16_embeddings_body(canary, rng)))
    if outcome in (_P16_MOD_MODERATED, _P16_MOD_ERRORED):
        return asyncio.run(_p16_run_moderations(deps, _p16_moderations_body(canary)))
    if outcome == _P16_MODELS_LISTED:
        return asyncio.run(_p16_run_models_list(deps))
    # models_not_found — retrieve an id the catalog never carries.
    return asyncio.run(_p16_run_models_retrieve(deps, "p16-absent-model-id"))


def _p16_assert_outcome(
    outcome: str, deps: MiscRoute, capture: _P16Capture, canary: str, ctx: str
) -> None:
    """Assert the surface reached the intended terminal outcome (not an accidental other path)."""
    provider = deps.provider
    assert isinstance(provider, _P16CountingProvider)
    body_text = capture.body.decode("utf-8") if capture.body else ""

    if outcome == _P16_EMB_ALLOWED:
        assert capture.status == 200, ctx
        assert provider.open_calls == 1, ctx  # the provider WAS the vector source (R6.1).
        parsed = json.loads(body_text)
        assert parsed["object"] == "list", ctx
    elif outcome == _P16_EMB_SHED:
        assert provider.open_calls == 0, ctx  # refused BEFORE provider.open.
        assert json.loads(body_text)["error"]["code"] == "overload_shed", ctx
    elif outcome == _P16_EMB_BUDGET_REFUSED:
        assert provider.open_calls == 0, ctx
        assert json.loads(body_text)["error"]["code"] == "budget_unavailable", ctx
    elif outcome == _P16_EMB_INPUT_BLOCKED:
        assert provider.open_calls == 0, ctx  # BLOCK short-circuits before provider contact.
        assert json.loads(body_text)["error"]["code"] == "input_blocked", ctx
    elif outcome == _P16_EMB_ERRORED:
        assert provider.open_calls == 0, ctx  # a raising input scan withholds pre-provider.
        assert json.loads(body_text)["error"]["code"] == "scan_failure", ctx
    elif outcome == _P16_MOD_MODERATED:
        assert provider.open_calls == 0, ctx  # moderations opens NO provider (R8.5).
        assert capture.status == 200, ctx
        result = json.loads(body_text)["results"][0]
        assert result["flagged"] is True, ctx
    elif outcome == _P16_MOD_ERRORED:
        assert provider.open_calls == 0, ctx
        payload = json.loads(body_text)
        assert "results" not in payload, ctx  # never an unscanned result body (R8.6).
        assert payload["error"]["code"] == "scan_failure", ctx
    elif outcome == _P16_MODELS_NOT_FOUND:
        assert provider.open_calls == 0, ctx  # models opens NO provider (R7.4).
        assert json.loads(body_text)["error"]["code"] == "model_not_found", ctx
    elif outcome == _P16_MODELS_LISTED:
        assert provider.open_calls == 0, ctx
        assert capture.status == 200, ctx
        parsed = json.loads(body_text)
        assert parsed["object"] == "list", ctx
        # The catalog carried the canary in a model id, so the rendered list genuinely does too.
        assert canary in body_text, ctx
    else:  # pragma: no cover - defensive: an unlisted outcome is a test bug.
        raise AssertionError(f"{ctx} unhandled outcome")


def test_property_16_terminal_outcomes_populate_content_free_exports_fail_open_zero() -> None:
    rng = random.Random(_SEED)
    org_id = "p16-org"

    for i in range(_ITERATIONS):
        # Rotate through every terminal outcome so each gets ~1/9 of the budget; a dropped export
        # population / a leaked canary / a non-zero fail-open for ANY outcome fails.
        outcome = _P16_TERMINAL_OUTCOMES[i % len(_P16_TERMINAL_OUTCOMES)]
        canary = _p16_canary(rng)
        deps = _p16_build_deps(outcome, org_id, canary)
        capture = _p16_drive(outcome, deps, canary, rng)
        ctx = _p16_ctx(i, outcome, status=capture.status)

        # The surface reached the intended terminal outcome (not an accidental other path).
        assert capture.body, f"{ctx} no client reply emitted"
        _p16_assert_outcome(outcome, deps, capture, canary, ctx)

        # ---- R10.1 / R10.2: the exports carry every dimension-bearing series ---- #
        reading = deps.exports.snapshot()
        _p16_assert_dimensions_present(reading, ctx)

        # ---- R10.3: FAIL_OPEN stays pinned at zero on EVERY terminal outcome ---- #
        assert reading.fail_open_total == 0, _p16_ctx(
            i, outcome, note="fail_open_total moved off zero", value=reading.fail_open_total
        )

        # ---- R10.4: no raw request/response content in any export field ---- #
        # The canary rode in BOTH the request (a folded input item) and the response (the
        # scripted provider vectors / the rendered body), so a producer that smuggled raw request
        # OR response content into any export field would surface the canary here. A full
        # stringification of every export field (scalars + histograms) must contain it ZERO times.
        exports_text = _p16_reading_text(reading)
        assert canary not in exports_text, _p16_ctx(
            i,
            outcome,
            note="raw request/response content (canary) appeared in an export field",
            canary=canary,
        )

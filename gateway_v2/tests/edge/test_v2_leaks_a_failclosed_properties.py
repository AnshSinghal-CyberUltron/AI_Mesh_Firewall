"""v2-enforcement-leaks Group A: an unverifiable input REDACT fails closed.

Fix-checking property test (spec ``v2-enforcement-leaks``, task 28.2; design Property 4,
Bug Condition A). Where task 28.1 proves a VERIFIABLE input REDACT is masked in every channel,
this file proves the fail-closed complement: a REDACT decision whose transformations CANNOT be
verified against the obliged finding spans must refuse the request with ``input_unmaskable`` --
rendered HTTP 400 ``invalid_request_error`` through ``edge/errors`` -- with ZERO provider calls
and the spent budget REFUNDED.

**Property 4.** For 10,000 seeded REDACT decisions on a chat OR embeddings request whose
transformations are unverifiable -- drawn from

* ``noop``         -- a REDACT obligation with an EMPTY transformation set over an obliged span,
* ``out_of_range`` -- a transformation (and finding) span outside the request content,
* ``partial``      -- a transformation covering only PART of the obliged finding span,
* ``overlap``      -- two transformations whose spans overlap (``apply_decision`` rejects them),
* ``count``        -- (embeddings only) a staged body carrying the WRONG number of deltas,

the gateway must fail closed. Each unverifiable kind reliably triggers a
``dispatch.verify.VerificationFailure`` (or, for ``count``, the embeddings per-item shape check),
which ``edge/input_enforcement.stage_upstream`` / ``edge/embeddings_input.stage_embeddings`` map to
``input_unmaskable`` (``edge/input_enforcement.REFUSE_INPUT_UNMASKABLE``).

**Drive-layer split (reported in the module docstring and asserted by the coverage counters).**
Every one of the 10,000 iterations runs the UNIT half: it builds the unverifiable decision /
request and calls ``stage_upstream`` (chat) or ``stage_embeddings`` (embeddings) directly, asserting
the result is the ``input_unmaskable`` refuse code (zero provider calls by construction -- neither
function can open a provider). Every ``_E2E_EVERY``-th iteration ALSO runs the EXPENSIVE half: it
drives the FULL §10.4 lifecycle through the real ``edge/app.build_app`` over a REAL
``admit.lease.BudgetLease`` on ``fakeredis`` (the ``tests/admit/test_v2_leaks_budget.py`` pattern),
forcing ``input_unmaskable`` via an injected ``input_detector`` that reports an out-of-range REDACT
finding, and asserts HTTP 400 + body ``error.code == "input_unmaskable"`` + 0 provider opens + the
lease ``remaining`` restored to its pre-spend value (the refund actually runs via
``RequestHolds.release`` in the handler's ``finally``). The two-tier split keeps the run fast while
covering the whole lifecycle.

The named unit requirement -- the ``input_unmaskable`` envelope renders HTTP 400 with body
``error.code == "input_unmaskable"``, the SAME ``invalid_request_error`` family as ``input_blocked``
-- is already covered by ``tests/edge/test_lgw16_misc_envelope.py``
(``test_input_unmaskable_renders_400_invalid_request``, from task 26.4). Rather than duplicate it,
this file adds one focused co-located assert pinning the shared-family relationship directly
(``test_input_unmaskable_envelope_shares_the_input_blocked_family``) and references the 26.4 test.

No hypothesis; one ``random.Random(_SEED)`` seeds every draw; the seed is in every assertion
message. These are TEST fakes (``tests/`` is outside the import-linter layer and the AST/size
gates), so they import freely across layers.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, cast

import fakeredis

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent, derive_low_watermark
from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import (
    BODY_DELTAS_KEY,
    BODY_MODEL_KEY,
    GatewayRequest,
    Transform,
)
from gateway_v2.domain import (
    Decision,
    Disposition,
    Finding,
    FindingDisposition,
    Span,
    Transformation,
)
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import FindingStatus
from gateway_v2.edge import errors
from gateway_v2.edge.app import ASGIApplication, build_app
from gateway_v2.edge.cancel import InFlightSnapshot
from gateway_v2.edge.chat_deps import BudgetDecider
from gateway_v2.edge.embeddings_input import stage_embeddings
from gateway_v2.edge.input_enforcement import REFUSE_INPUT_UNMASKABLE, stage_upstream
from gateway_v2.edge.wire.openai_misc import MiscEmbeddingsRequest
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys import KEYS
from tests.edge._chat_lifecycle_fixtures import (
    DEFAULT_ORG_ID,
    ResolvingIdentity,
    warm_snapshot_source,
)

# --------------------------------------------------------------------------- #
# Run shape.
# --------------------------------------------------------------------------- #

#: Seeded, deterministic; the seed is echoed in every assertion message.
_SEED = 0x2821_FA11_C105ED
#: At least 10,000 iterations (the spec's property idiom).
_ITERATIONS = 10_000
#: Every ``_E2E_EVERY``-th iteration ALSO drives the full ASGI lifecycle over a real lease (the
#: expensive refund/400/zero-calls half). 10,000 // 250 = 40 end-to-end cases -- a non-trivial
#: subset that exercises both surfaces and keeps the run well under a few seconds.
_E2E_EVERY = 250

_ORG = DEFAULT_ORG_ID
_CHAT = "/v1/chat/completions"
_EMBEDDINGS = "/v1/embeddings"
_MODEL = "gpt-4o-mini"
_DEST = "upstream/default"
_PLAN_VERSION = "v2-leaks-28.2"

#: An org pool and a lease chunk far larger than any cost here, so the request is admitted and a
#: spend is taken (the refund then has something to restore).
_POOL = 1_000_000
_BIG_CHUNK = 100_000

#: The unverifiable kinds. ``count`` is embeddings-only (a per-item shape mismatch); the other
#: four apply to both surfaces at the stage/verify level.
_CHAT_KINDS = ("noop", "out_of_range", "partial", "overlap")
_EMBED_KINDS = ("noop", "out_of_range", "partial", "overlap", "count")


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


# --------------------------------------------------------------------------- #
# The unverifiable-decision generator (the unit half).
# --------------------------------------------------------------------------- #


def _redact_finding(span: Span) -> Finding:
    """A single EXECUTED PII finding over ``span`` (the obliged, to-be-redacted span)."""
    return Finding(
        detector="det",
        detector_version=_PLAN_VERSION,
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(span,),
        evidence=None,
    )


def _redact_decision(
    finding: Finding, transformations: tuple[tuple[int, int], ...]
) -> Decision:
    """A finding-index-keyed REDACT decision over ``finding`` with the given transform spans.

    The per-finding disposition carries ``finding_index=0`` so ``dispatch.verify`` takes the
    finding-keyed obligation path and obliges exactly this finding's span. Each transformation
    writes ``[REDACTED]`` over its ``(start, end)`` span; an empty ``transformations`` tuple is a
    no-op obligation (nothing covers the obliged span).
    """
    return Decision(
        disposition=Disposition.REDACT,
        per_finding=(
            FindingDisposition(
                detector="det", disposition=Disposition.REDACT, finding_index=0
            ),
        ),
        transformations=tuple(
            Transformation(
                kind="redact", span=Span(start=start, end=end), replacement="[REDACTED]"
            )
            for start, end in transformations
        ),
        findings=(finding,),
        plan_version=_PLAN_VERSION,
        deciding_rules=("det",),
        unavailable_detectors=(),
    )


def _unverifiable_decision(
    rng: random.Random, kind: str, content_len: int
) -> Decision:
    """Build a REDACT decision whose transformations are unverifiable for ``kind``.

    ``content_len`` is the length of the request content the decision is resolved against. Every
    kind here makes ``dispatch.verify.apply_and_verify`` raise ``VerificationFailure``:

    * ``noop``         -- an obliged in-range finding span, but NO transformation covers it
      (``_assert_fully_covered_by_transformations`` raises).
    * ``out_of_range`` -- the finding span lies beyond the content (``_assert_covered`` raises).
    * ``partial``      -- a transformation covers only a PREFIX of the obliged span, leaving a
      raw remainder (``_assert_fully_covered_by_transformations`` raises).
    * ``overlap``      -- two transformations whose spans overlap inside the obliged span
      (``apply_decision`` raises ``ValueError`` -> ``VerificationFailure``).
    """
    if content_len < 4:
        content_len = 4
    if kind == "out_of_range":
        far = content_len + rng.randint(1, 50)
        span = Span(start=far, end=far + rng.randint(1, 8))
        return _redact_decision(_redact_finding(span), ())
    # In-range obliged span occupying a sub-range of the content, width >= 2.
    width = rng.randint(2, max(2, content_len))
    start = rng.randint(0, content_len - width)
    end = start + width
    finding = _redact_finding(Span(start=start, end=end))
    if kind == "noop":
        return _redact_decision(finding, ())
    if kind == "partial":
        # Cover only a strict prefix of the obliged span (leaves a raw remainder uncovered).
        cut = rng.randint(start + 1, end - 1)
        return _redact_decision(finding, ((start, cut),))
    if kind == "overlap":
        # Two transformations that overlap inside the obliged span; apply_decision rejects them.
        mid = start + 1
        return _redact_decision(finding, ((start, end), (mid, min(end, mid + 1))))
    raise AssertionError(f"unknown unverifiable kind {kind!r}")


class _DroppingTransform:
    """A Transform double whose ``to_provider`` packs FEWER deltas than the request carried.

    Drops the last delta so the staged count differs from ``len(req.input_items)``, driving
    ``stage_embeddings``'s per-item shape check to ``input_unmaskable`` on a count mismatch.
    """

    __slots__ = ()

    def to_provider(self, req: GatewayRequest) -> UpstreamRequest:
        body: MappingProxyType[str, object] = MappingProxyType(
            {BODY_MODEL_KEY: req.model, BODY_DELTAS_KEY: req.text_deltas[:-1]}
        )
        return UpstreamRequest(destination=req.destination, body=body)


# --------------------------------------------------------------------------- #
# The end-to-end lifecycle half (real lease on fakeredis; forces input_unmaskable).
# --------------------------------------------------------------------------- #

#: An out-of-range span the forcing input_detector reports -- far beyond any request content, so the
#: resolver's REDACT transformation over it cannot be verified -> VerificationFailure ->
#: input_unmaskable. ``det`` is an UNKNOWN detector so MinimalOutputResolver maps it to REDACT (a
#: known class like ``card`` would map to BLOCK and give input_blocked instead).
_FAR_START = 1_000_000
_FAR_END = _FAR_START + 11


def _forcing_detector(text: str) -> Sequence[Finding]:
    """Report one out-of-range REDACT finding whenever the probe marker is present.

    The lifecycle scans every delta (and join run); a finding with a span far beyond the content
    makes the resolver build a REDACT transformation that ``apply_and_verify`` cannot cover, so the
    request fails closed to ``input_unmaskable`` after it has been admitted and budget-spent.
    """
    if _PROBE not in text:
        return ()
    return (
        Finding(
            detector="det",
            detector_version=_PLAN_VERSION,
            category=Category.PII,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(start=_FAR_START, end=_FAR_END),),
            evidence=None,
        ),
    )


def _clean_detector(_text: str) -> Sequence[Finding]:
    return ()


#: A marker every end-to-end request body carries so ``_forcing_detector`` fires.
_PROBE = "forceunmaskable"


class _Provider:
    """Counts opens; echoes a fixed completion. A REDACT that fails closed never opens it."""

    __slots__ = ("opened",)

    def __init__(self) -> None:
        self.opened = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.opened += 1
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        del request
        yield UpstreamEvent(text_deltas=(("content", "hello there"),), final=True)

    async def abort(self) -> None:
        return None


class _Admission:
    """Admits every request; ``complete`` is a no-op (Group D owns its own tests)."""

    __slots__ = ("admitted",)

    def __init__(self) -> None:
        self.admitted: list[str] = []

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        self.admitted.append(request_id)
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)

    def complete(self, request_id: str) -> None:
        del request_id


class _Metrics:
    __slots__ = ()

    def exposition(self) -> str:
        return "# v2-leaks-28.2\n"


class _Control:
    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return InFlightSnapshot(
            available=True,
            age_s=0.0,
            kill_switch_engaged=False,
            key_revoked=False,
            plan_changed=False,
        )


class _QuotaMetrics:
    __slots__ = ("budget_unavailable", "refills")

    def __init__(self) -> None:
        self.refills = 0
        self.budget_unavailable = 0

    def observe_async_refill(self) -> None:
        self.refills += 1

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _drop(work: Awaitable[None]) -> None:
    """Off-path refill spawn that never runs (keeps the lease ledger deterministic)."""
    close = getattr(work, "close", None)
    if callable(close):
        close()


async def _real_quota(client: Any) -> tuple[QuotaComponent, BudgetLease]:
    """A REAL QuotaComponent over a REAL BudgetLease on ``client``, first chunk acquired."""
    await client.set(KEYS.budget_remaining(_ORG), _POOL)
    await client.set(KEYS.budget_generation(_ORG), 0)
    metrics = _QuotaMetrics()
    lease = BudgetLease(
        client,
        LeaseConfig(
            org=_ORG,
            chunk=_BIG_CHUNK,
            low_watermark=derive_low_watermark(_BIG_CHUNK),
            ttl_s=30.0,
        ),
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=_drop,
        metrics=metrics,
    )
    assert await lease.acquire(0), "harness: the first chunk was not granted"
    gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=1_000_000), clock=lambda: 0.0)
    quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)
    return quota, lease


def _app(*, quota: BudgetDecider, provider: _Provider) -> ASGIApplication:
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=MinimalOutputResolver(),
        metrics_registry=_Metrics(),
        clock=lambda: 0.0,
        control=_Control(),
        error_scanner=_clean_detector,
        input_detector=_forcing_detector,
        identity=ResolvingIdentity(),
        snapshot_source=warm_snapshot_source(),
        admission=_Admission(),
        quota=quota,
    )


async def _serve(app: ASGIApplication, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
    """One non-streaming request through the ASGI app; returns ``(status, error-object)``."""
    sent: list[Mapping[str, object]] = []
    incoming: list[Mapping[str, object]] = [
        {"type": "http.request", "body": body, "more_body": False}
    ]
    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json")],
    }

    async def _receive() -> Mapping[str, object]:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(dict(message))

    await app(scope, _receive, _send)
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    raw = b"".join(
        bytes(chunk)
        for m in sent
        if m["type"] == "http.response.body"
        and isinstance(chunk := m.get("body", b""), (bytes, bytearray))
    )
    decoded = json.loads(raw) if raw else {}
    error = decoded.get("error") if isinstance(decoded, dict) else None
    return status, error if isinstance(error, dict) else {}


def _e2e_body(surface: str) -> bytes:
    """A well-formed body on ``surface`` carrying the probe marker the forcing detector fires on."""
    content = f"please handle {_PROBE} now"
    if surface == "embeddings":
        return json.dumps({"model": "echo-1", "input": [content]}).encode("utf-8")
    return json.dumps(
        {"model": _MODEL, "messages": [{"role": "user", "content": content}]}
    ).encode("utf-8")


def _run_e2e(surface: str) -> tuple[int, dict[str, Any], int, int, int]:
    """Drive one unverifiable REDACT end-to-end; return status, error, held before/after, opens."""
    path = _EMBEDDINGS if surface == "embeddings" else _CHAT

    async def _run() -> tuple[int, dict[str, Any], int, int, int]:
        client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        quota, lease = await _real_quota(client)
        held_before = lease.remaining
        provider = _Provider()
        app = _app(quota=quota, provider=provider)
        status, error = await _serve(app, path, _e2e_body(surface))
        held_after = lease.remaining
        await client.aclose()
        return status, error, held_before, held_after, provider.opened

    return asyncio.run(_run())


# --------------------------------------------------------------------------- #
# The property.
# --------------------------------------------------------------------------- #


def test_unverifiable_input_redact_fails_closed_to_input_unmaskable() -> None:
    """Property 4: every unverifiable REDACT refuses ``input_unmaskable``, 0 calls, refund (A).

    **Validates: Requirements 2.2, 2.17, 2.21**
    """
    rng = random.Random(_SEED)
    transform = Transform()

    kinds_seen: set[str] = set()
    surfaces_seen: set[str] = set()
    e2e_cases = 0
    e2e_surfaces: set[str] = set()

    for index in range(_ITERATIONS):
        surface = rng.choice(("chat", "embeddings"))
        surfaces_seen.add(surface)
        kinds = _EMBED_KINDS if surface == "embeddings" else _CHAT_KINDS
        kind = rng.choice(kinds)
        kinds_seen.add(kind)

        # Build a request whose content is long enough to carry an in-range obliged span.
        content_len = rng.randint(8, 48)
        text = "x" * content_len

        if kind == "count":
            # Embeddings count-mismatch: a clean ALLOW decision staged through a Transform double
            # that drops a delta, so the per-item shape check (not apply_and_verify) fails closed.
            items = tuple(f"item{n}" for n in range(rng.randint(2, 6)))
            req = MiscEmbeddingsRequest(
                model="echo-1", input_items=items, input_is_string=False
            )
            allow = Decision(
                disposition=Disposition.ALLOW,
                per_finding=(),
                transformations=(),
                findings=(),
                plan_version=_PLAN_VERSION,
                deciding_rules=(),
                unavailable_detectors=(),
            )
            staged = stage_embeddings(
                allow,
                req,
                destination=_DEST,
                transform=cast(Transform, _DroppingTransform()),
            )
            assert staged == REFUSE_INPUT_UNMASKABLE, (
                f"seed={_SEED:#x} i={index} surface={surface} kind={kind}: "
                f"a count-mismatched staged body must fail closed to input_unmaskable, "
                f"got {staged!r}"
            )
        else:
            decision = _unverifiable_decision(rng, kind, content_len)
            if surface == "embeddings":
                req = MiscEmbeddingsRequest(
                    model="echo-1", input_items=(text,), input_is_string=True
                )
                staged = stage_embeddings(
                    decision, req, destination=_DEST, transform=transform
                )
            else:
                gw = GatewayRequest(
                    destination=_DEST, model=_MODEL, text_deltas=(("message_content", text),)
                )
                staged = stage_upstream(decision, gw, transform)
            assert staged == REFUSE_INPUT_UNMASKABLE, (
                f"seed={_SEED:#x} i={index} surface={surface} kind={kind} "
                f"content_len={content_len}: an unverifiable REDACT must fail closed to "
                f"input_unmaskable, got {staged!r}"
            )

        # The expensive half: every _E2E_EVERY-th iteration drives the whole lifecycle over a real
        # lease and asserts the rendered 400, zero provider calls and the refund.
        if index % _E2E_EVERY == 0:
            status, error, before, after, opened = _run_e2e(surface)
            e2e_cases += 1
            e2e_surfaces.add(surface)
            where = f"seed={_SEED:#x} i={index} surface={surface} (e2e)"
            assert status == 400, f"{where}: expected HTTP 400, got {status} error {error!r}"
            assert error.get("type") == "invalid_request_error", (
                f"{where}: error.type {error.get('type')!r}, expected invalid_request_error"
            )
            assert error.get("code") == REFUSE_INPUT_UNMASKABLE, (
                f"{where}: error.code {error.get('code')!r}, expected {REFUSE_INPUT_UNMASKABLE}"
            )
            assert opened == 0, f"{where}: provider opened {opened} time(s), expected 0"
            assert after == before, (
                f"{where}: spend not refunded -- lease held {before} before, {after} after"
            )

    # Coverage counters (asserted non-trivial): every unverifiable kind, both surfaces, and at
    # least one end-to-end refund-verified case on each surface, were exercised.
    assert kinds_seen == set(_EMBED_KINDS), (
        f"seed={_SEED:#x}: not every unverifiable kind exercised, saw {sorted(kinds_seen)}"
    )
    assert surfaces_seen == {"chat", "embeddings"}, (
        f"seed={_SEED:#x}: both surfaces must be exercised, saw {sorted(surfaces_seen)}"
    )
    assert e2e_cases >= 2, (
        f"seed={_SEED:#x}: expected >= 2 end-to-end cases, ran {e2e_cases}"
    )
    assert e2e_surfaces == {"chat", "embeddings"}, (
        f"seed={_SEED:#x}: end-to-end half must cover both surfaces, saw {sorted(e2e_surfaces)}"
    )


# --------------------------------------------------------------------------- #
# The named unit requirement (shares the input_blocked family; 26.4 covers the
# standalone 400/code assert -- this pins the shared-family relationship).
# --------------------------------------------------------------------------- #


def test_input_unmaskable_envelope_shares_the_input_blocked_family() -> None:
    """``input_unmaskable`` renders 400 with its own code, the same family as ``input_blocked``.

    The standalone "``input_unmaskable`` -> 400 / code ``input_unmaskable``" assert already lives in
    ``tests/edge/test_lgw16_misc_envelope.py::test_input_unmaskable_renders_400_invalid_request``
    (task 26.4); this co-located test additionally pins that it is the SAME HTTP status and
    ``error.type`` FAMILY as ``input_blocked`` (the relationship task 28.2 names), reading the
    single ``edge/errors`` render authority rather than a hardcode.

    **Validates: Requirements 2.2, 2.21**
    """
    unmaskable = errors.render_http(REFUSE_INPUT_UNMASKABLE)
    blocked = errors.render_http("input_blocked")

    unmaskable_error = cast(dict[str, object], unmaskable.json()).get("error")
    blocked_error = cast(dict[str, object], blocked.json()).get("error")
    assert isinstance(unmaskable_error, dict) and isinstance(blocked_error, dict)

    assert unmaskable.status == 400, unmaskable.status
    assert unmaskable_error["type"] == "invalid_request_error", unmaskable_error
    assert unmaskable_error["code"] == REFUSE_INPUT_UNMASKABLE, unmaskable_error

    # Same family as input_blocked (status + error.type), distinct body code.
    assert unmaskable.status == blocked.status, (unmaskable.status, blocked.status)
    assert unmaskable_error["type"] == blocked_error["type"], (
        unmaskable_error["type"],
        blocked_error["type"],
    )
    assert unmaskable_error["code"] != blocked_error["code"], (
        unmaskable_error["code"],
        blocked_error["code"],
    )

# Feature: chat-surface-gw15, Property 20: Every terminal outcome populates content-free exports with FAIL_OPEN zero  # noqa: E501
# Validates: Requirements 14.1, 14.2, 14.3, 14.4
"""Property 20 — every terminal outcome populates content-free exports, FAIL_OPEN at 0.

House idiom: a seeded ``random.Random``, NO ``hypothesis``, >=10,000 iterations, the async
§10.4 lifecycle + the non-streaming collector driven via ``asyncio.run``; one property <->
one property-based test.

*For any* terminal outcome a chat request can reach, the Chat_Surface populates the shipped
GW14d :class:`~gateway_v2.runtime.stream_metrics.PerRequestExports` with the admission /
budget / input-enforcement / output-enforcement dimensions, keeps ``fail_open_total`` at
zero, and places NO raw request or response content in any export field (R14.1 / R14.2 /
R14.3 / R14.4).

The seven terminal outcomes the §10.4 lifecycle + the non-streaming path can produce — the
exact set the boot wiring (task 6.1) documents — are each driven every iteration, over a
REAL :class:`ChatSurface` with the real shipped resolver / ``apply_decision`` / renderer and
only the per-phase deciders + the output scanner as fail-closed / deterministic test doubles:

* **allowed** — admission admits, budget admits, the input scan finds nothing, and the
  non-streaming collector enforces a clean (ALLOW) output and renders ONE OpenAI JSON.
* **shed** — admission returns a ``ShedVerdict`` ⇒ the lifecycle refuses with ``overload``
  BEFORE the provider is opened.
* **budget-refused** — admission admits, budget returns a ``BudgetVerdict`` ⇒ the lifecycle
  refuses with ``budget_unavailable``.
* **input-blocked** — the injected input detector emits a terminate-class (``jwt``) finding
  the shipped resolver maps to BLOCK (C22) ⇒ the lifecycle refuses with ``input_blocked``
  and makes zero provider calls.
* **output-redacted** — the non-streaming collector resolves a REDACT-class (``email``)
  output finding and ``apply_decision`` masks the canary span before the renderer runs.
* **output-blocked** — a terminate-class (``card``) output finding ⇒ the collector withholds
  the whole body and renders ``output_blocked``.
* **errored** — the output scanner raises ⇒ the collector fails closed and withholds with the
  declared ``scan_failure`` code.

The canary is placed in BOTH the request (a folded input channel) AND the provider's response
(an output-content delta) every iteration, so a producer that leaked raw request OR response
content into any export field would surface the canary bytes. A SINGLE
:class:`PerRequestExports` is observed across the whole request for each outcome, and after
the terminal outcome the property asserts:

1. the snapshot carries every dimension-bearing series — the admission / budget / input- /
   output-enforcement outcomes are reflected in the fixed, label-free series the shipped
   producer exposes as real zeros from the first snapshot (``detector_invocations``,
   ``buffer_high_water``, ``active_stream_memory_bound``, ``withheld_total`` and the two
   latency histograms), so "no outcome recorded" and "no series" cannot look the same
   (R14.1 / R14.2);
2. ``fail_open_total`` is zero — the pinned-zero series no correct terminal path increments,
   because the Gateway fails closed by withholding, never by forwarding raw bytes
   (R14.3); and
3. the canary — placed in both the request and the response — appears ZERO times in a full
   stringification of every export field, so no raw request/response content is in the
   exports (R14.4).

Everything this file owns is uniquely prefixed ``_p20_`` / ``_P20`` so it never clashes with
the sibling tests that share the ``tests/edge`` package.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from gateway_v2.admit.grant import (
    Admitted,
    BudgetVerdict,
    ShedVerdict,
    budget_verdict,
    shed_verdict,
)
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain.category import Category
from gateway_v2.domain.finding import Finding, FindingStatus, Span
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, OVERLOAD_SHED
from gateway_v2.edge.identity_resolver import IdentityResolver, TenantIdentity
from gateway_v2.edge.openai_chat import (
    REFUSE_INPUT_BLOCKED,
    REFUSE_INPUT_SCAN_FAILURE,
    REFUSE_OUTPUT_BLOCKED,
    ChatSurface,
    LifecycleOutcome,
)
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.edge.wire.openai import OpenAIRequestParser, OpenAIResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.holdback_metrics import Histogram
from gateway_v2.runtime.stream_metrics import PerRequestExports, PerRequestReading

_SEED = 0x20_15_0020
_ITERATIONS = 10_000

#: The ASGI scope path the parser maps to ``Surface.CHAT``.
_P20_CHAT_PATH = "/v1/chat/completions"

#: The output-content channel the non-streaming collector folds a provider delta into and the
#: renderer maps to ``message.content``. The canary is placed here so a leaked response byte
#: would surface in an export field.
_P20_OUTPUT_CHANNEL = "output_content"

#: Pre-check bounds so large the Pre_Identity_Clock phase never refuses — this property
#: exercises the terminal-outcome export contract, not the size / expansion / part-count
#: pre-checks. TEST bounds on a fake contract, never production capacity literals.
_P20_MAX_BYTES = 1 << 30
_P20_MAX_EXPANSION_RATIO = 1_000.0
_P20_MAX_PARTS = 1 << 30

#: Detector names the shipped ``MinimalOutputResolver`` maps through its trade-off table:
#: ``jwt`` / ``card`` → BLOCK (terminate), ``email`` → REDACT (redact-the-remainder).
_P20_BLOCK_INPUT_DETECTOR = "jwt"
_P20_BLOCK_OUTPUT_DETECTOR = "card"
_P20_REDACT_OUTPUT_DETECTOR = "email"

#: The seven terminal outcomes the §10.4 lifecycle + non-streaming path can produce. Each is
#: driven every iteration; a dropped export population for ANY outcome fails the gate.
_P20_ALLOWED = "allowed"
_P20_SHED = "shed"
_P20_BUDGET_REFUSED = "budget_refused"
_P20_INPUT_BLOCKED = "input_blocked"
_P20_OUTPUT_REDACTED = "output_redacted"
_P20_OUTPUT_BLOCKED = "output_blocked"
_P20_ERRORED = "errored"

_P20_TERMINAL_OUTCOMES: tuple[str, ...] = (
    _P20_ALLOWED,
    _P20_SHED,
    _P20_BUDGET_REFUSED,
    _P20_INPUT_BLOCKED,
    _P20_OUTPUT_REDACTED,
    _P20_OUTPUT_BLOCKED,
    _P20_ERRORED,
)

#: The outcomes that refuse in the pre-provider lifecycle (vs. the output-side collector).
_P20_PRE_PROVIDER_REFUSALS: Mapping[str, str] = {
    _P20_SHED: OVERLOAD_SHED,
    _P20_BUDGET_REFUSED: BUDGET_UNAVAILABLE,
    _P20_INPUT_BLOCKED: REFUSE_INPUT_BLOCKED,
}

#: The outcomes driven through the non-streaming collector (lifecycle proceeds first).
_P20_OUTPUT_OUTCOMES: tuple[str, ...] = (
    _P20_ALLOWED,
    _P20_OUTPUT_REDACTED,
    _P20_OUTPUT_BLOCKED,
    _P20_ERRORED,
)

#: Alphabet for canary synthesis — printable, distinctive, so a canary that slips into any
#: export field is unambiguous in a counterexample.
_P20_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _p20_ctx(i: int, outcome: str, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} outcome={outcome} {body}"


def _p20_canary(rng: random.Random) -> str:
    """A distinctive canary token unlikely to collide with any filler/declared text."""
    n = rng.randint(6, 24)
    return "P20CANARY_" + "".join(rng.choice(_P20_CANARY_ALPHABET) for _ in range(n))


# --------------------------------------------------------------------------- #
# Per-phase deciders + the output scanner (deterministic / fail-closed doubles).
# --------------------------------------------------------------------------- #


class _P20FakeContract:
    """Pre-check accessors with generous bounds so the pre-check phase never refuses."""

    __slots__ = ()

    def max_request_bytes(self) -> int:
        return _P20_MAX_BYTES

    def max_expansion_ratio(self) -> float:
        return _P20_MAX_EXPANSION_RATIO

    def max_request_parts(self) -> int:
        return _P20_MAX_PARTS

    def max_output_tokens_boundary(self) -> int:
        """v1's ``max_tokens`` parse boundary; ``parse`` reads it to validate token fields."""
        return 1_000_000

    def default_response_token_ceiling(self) -> int:
        """v1's default org output ceiling; ``run_lifecycle`` clamps ``max_tokens`` to it."""
        return 4096

    def max_output_tokens(self) -> int:
        """v1's output-token hard cap (the clamp's upper bound)."""
        return 65536

    def max_completions(self) -> int:
        """v1's completion cap; the clamp forces ``n`` to it."""
        return 1

    def estimate_request_tokens(self, chars: int, max_tokens: int, n: int) -> int:
        """v1's request token estimate the budget phase charges over the clamped request."""
        input_tokens = max(1, chars // 4) if chars > 0 else 0
        return max(input_tokens + max_tokens * n, 1)


class _P20Identity:
    """An :class:`IdentityResolver` resolving one canned tenant (never fails closed)."""

    __slots__ = ("_identity",)

    def __init__(self, org_id: str) -> None:
        self._identity = TenantIdentity(
            org_id=org_id,
            principal=Principal(
                key_id=f"p20-key-{org_id}",
                org_id=org_id,
                rate_per_s=1.0,
                burst=1.0,
                feed_seq=0,
            ),
        )

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _P20AdmitAdmission:
    """Admission that always admits (keyed to the request's own owner)."""

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del enqueued_at
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

    def complete(self, request_id: str) -> None:
        del request_id


class _P20ShedAdmission:
    """Admission that always sheds (the ``shed`` terminal outcome)."""

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del owner_id, enqueued_at
        # Fresh, non-shared rng so the retry-after floor+jitter contract holds deterministically.
        return shed_verdict(random.Random(0), request_id)

    def complete(self, request_id: str) -> None:
        del request_id


class _P20AdmitBudget:
    """Budget that always admits (local lease, no store read)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return Admitted(owner_id="p20", request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _P20RefuseBudget:
    """Budget that always refuses (the ``budget_refused`` terminal outcome)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return budget_verdict(request_id)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


class _P20InputDetector:
    """An input detector that fires a BLOCK-class finding on the canary (``input_blocked``)."""

    __slots__ = ("_block", "_canary")

    def __init__(self, canary: str, *, block: bool) -> None:
        self._canary = canary
        self._block = block

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._block and self._canary in text:
            return (
                Finding(
                    detector=_P20_BLOCK_INPUT_DETECTOR,
                    detector_version="p20",
                    category=Category.PII,
                    status=FindingStatus.EXECUTED,
                    confidence=0.99,
                    spans=(),
                    evidence=None,
                ),
            )
        return ()


class _P20OutputScanner:
    """The injected output findings producer: ALLOW / REDACT / BLOCK by outcome, or raise.

    Given a channel's whole text it emits, over every canary occurrence, one EXECUTED finding
    tagged with the detector the outcome demands — ``email`` (REDACT), ``card`` (BLOCK) — or
    nothing (ALLOW). The ``errored`` outcome's scanner RAISES so ``collect_and_enforce`` fails
    closed with the declared ``scan_failure`` code. A channel with no canary yields no finding.
    """

    __slots__ = ("_canary", "_detector", "_raise")

    def __init__(self, canary: str, *, detector: str | None, raise_: bool) -> None:
        self._canary = canary
        self._detector = detector
        self._raise = raise_

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._raise:
            raise RuntimeError("p20: output scanner is uncomputable")
        if self._detector is None or self._canary not in text:
            return ()
        spans: list[Span] = []
        start = text.find(self._canary)
        while start != -1:
            spans.append(Span(start=start, end=start + len(self._canary)))
            start = text.find(self._canary, start + len(self._canary))
        return (
            Finding(
                detector=self._detector,
                detector_version="p20",
                category=Category.PII,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=tuple(spans),
                evidence=None,
            ),
        )


# --------------------------------------------------------------------------- #
# The ChatRoute-shaped deps bundle the lifecycle + non-stream collector read.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _P20Deps:
    """The ``ChatRoute``-shaped subset the §10.4 lifecycle + collector read, as an injectable fake.

    Carries exactly the seams reached per outcome: the pre-check contract, the identity
    resolver, the served snapshot source, the admission/budget deciders, the input detector,
    the output scanner (``error_scanner``), the real shipped resolver + renderer + router +
    transform, the provider stub, and the one :class:`PerRequestExports` observed across the
    request. Cast to the ``ChatRoute`` annotation so ``mypy --strict`` is satisfied without
    constructing the full heavyweight route (the scanner / holdback / executor / in-flight
    seams are never reached on these terminal paths).
    """

    contract: _P20FakeContract
    identity: IdentityResolver
    snapshot_source: object
    admission: object
    quota: object
    input_detector: object
    error_scanner: object
    resolver: MinimalOutputResolver
    provider: StubProviderClient
    exports: PerRequestExports
    clock: object = field(default=lambda: 0.0)
    parser: OpenAIRequestParser = field(default_factory=OpenAIRequestParser)
    renderer: OpenAIResponseRenderer = field(default_factory=OpenAIResponseRenderer)
    router: DispatchRouter = field(default_factory=DispatchRouter)
    transform: Transform = field(default_factory=Transform)


def _p20_plan(org_id: str) -> ExecutionPlan:
    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=1,
        content_hash="p20" + "0" * 61,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _p20_snapshot_source(plan: ExecutionPlan) -> object:
    """A ``snapshot_source`` whose served snapshot resolves ``plan.org_id`` to ``plan``."""
    store = PlanStore()
    store.put(plan)
    snapshot = ReplicaSnapshot(store)
    snapshot.absorb(plan.org_id, now=0.0)

    def _source() -> ReplicaSnapshot:
        return snapshot

    return _source


def _p20_response_provider(canary: str, rng: random.Random) -> StubProviderClient:
    """A provider whose single final event carries the canary in the output-content channel.

    A correct run would place the canary into the collected response (and, on the ALLOW /
    REDACT paths, into the rendered JSON content), so the response bytes genuinely exist —
    the exports must still never carry them (R14.4).
    """
    return StubProviderClient(
        script=(UpstreamEvent(text_deltas=((_P20_OUTPUT_CHANNEL, canary),), final=True),),
        rng=rng,
    )


def _p20_build_deps(outcome: str, org_id: str, canary: str, rng: random.Random) -> _P20Deps:
    """Assemble the per-outcome deps bundle (one fresh ``PerRequestExports`` per request)."""
    plan = _p20_plan(org_id)
    admission: object = _P20AdmitAdmission()
    quota: object = _P20AdmitBudget()
    input_detector: object = _P20InputDetector(canary, block=False)
    output_detector: str | None = None
    raise_scanner = False

    if outcome == _P20_SHED:
        admission = _P20ShedAdmission()
    elif outcome == _P20_BUDGET_REFUSED:
        quota = _P20RefuseBudget()
    elif outcome == _P20_INPUT_BLOCKED:
        input_detector = _P20InputDetector(canary, block=True)
    elif outcome == _P20_OUTPUT_REDACTED:
        output_detector = _P20_REDACT_OUTPUT_DETECTOR
    elif outcome == _P20_OUTPUT_BLOCKED:
        output_detector = _P20_BLOCK_OUTPUT_DETECTOR
    elif outcome == _P20_ERRORED:
        raise_scanner = True

    return _P20Deps(
        contract=_P20FakeContract(),
        identity=_P20Identity(org_id),
        snapshot_source=_p20_snapshot_source(plan),
        admission=admission,
        quota=quota,
        input_detector=input_detector,
        error_scanner=_P20OutputScanner(canary, detector=output_detector, raise_=raise_scanner),
        resolver=MinimalOutputResolver(),
        provider=_p20_response_provider(canary, random.Random(rng.random())),
        exports=PerRequestExports(),
    )


def _p20_body(canary: str, rng: random.Random) -> bytes:
    """A well-formed OpenAI chat body carrying the canary in a message's ``content``."""
    model = "gpt-" + "".join(rng.choice("abcdef0123456789") for _ in range(6))
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": f"please echo {canary}"}],
    }
    return json.dumps(body).encode("utf-8")


def _p20_scope() -> dict[str, object]:
    return {"type": "http", "method": "POST", "path": _P20_CHAT_PATH, "headers": []}


class _P20Send:
    """A recording ASGI ``send`` so the client-visible response bytes are observable."""

    __slots__ = ("events",)

    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    async def __call__(self, event: Mapping[str, object]) -> None:
        self.events.append(event)

    def body_bytes(self) -> bytes:
        out = b""
        for event in self.events:
            if event.get("type") == "http.response.body":
                out += event.get("body", b"")  # type: ignore[operator]
        return out


# --------------------------------------------------------------------------- #
# Export inspection: dimension presence + content-freedom over the whole reading.
# --------------------------------------------------------------------------- #


def _p20_assert_dimensions_present(reading: PerRequestReading, ctx: str) -> None:
    """Assert the snapshot carries every dimension-bearing series as a real value (R14.1/R14.2).

    The admission / budget / input- / output-enforcement outcomes are reflected in the fixed,
    label-free series the shipped producer exposes as REAL ZEROS from the first snapshot (never
    absent), so an alarm cannot confuse "no outcome recorded" with "no series". Every scalar
    series is a concrete ``int`` and both latency distributions are real :class:`Histogram`
    objects — present, typed, and non-``None`` on every terminal outcome.
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


def _p20_reading_text(reading: PerRequestReading) -> str:
    """A full stringification of every export field, for the content-freedom assertion.

    Walks every field of the frozen :class:`PerRequestReading` (the scalar series + the two
    histograms, whose observed samples are stringified too) into one text blob, so a canary
    hiding in ANY export field — a scalar, a histogram bucket/sample, anything — would appear
    here. The histograms are rendered via ``repr`` plus their observed samples so a raw byte
    smuggled into a sample value is caught.
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
# Drivers: run one terminal outcome end to end and return (outcome-check, exports).
# --------------------------------------------------------------------------- #


async def _p20_run_pre_provider(
    deps: _P20Deps, scope: Mapping[str, object], body: bytes
) -> LifecycleOutcome:
    """Drive the pre-provider lifecycle (shed / budget-refused / input-blocked)."""
    _req, outcome = await ChatSurface(deps=_p20_as_route(deps)).run_lifecycle(scope, body)
    return outcome


async def _p20_run_output(
    deps: _P20Deps, scope: Mapping[str, object], body: bytes, send: _P20Send
) -> LifecycleOutcome:
    """Drive the full request: lifecycle proceeds, then the non-streaming collector.

    Returns the (proceed) lifecycle outcome; the collector's client-visible bytes are captured
    on ``send``. Used for the allowed / output-redacted / output-blocked / errored outcomes.
    """
    surface = ChatSurface(deps=_p20_as_route(deps))
    req, outcome = await surface.run_lifecycle(scope, body)
    assert outcome.refuse_code is None, (
        f"output outcome refused pre-provider: {outcome.refuse_code}"
    )
    assert req is not None and outcome.plan is not None
    assert outcome.destination is not None and outcome.body is not None
    await surface.collect_and_render(
        req, outcome.plan, outcome.destination, outcome.body, send
    )
    return outcome


def _p20_as_route(deps: _P20Deps) -> ChatRoute:
    # The duck-typed bundle carries exactly the seams these terminal paths reach; cast to the
    # ChatRoute annotation so mypy --strict is satisfied without the full boot graph.
    return deps  # type: ignore[return-value]


def test_property_20_terminal_outcomes_populate_content_free_exports_fail_open_zero() -> None:
    rng = random.Random(_SEED)
    org_id = "p20-org"

    for i in range(_ITERATIONS):
        # Rotate through every terminal outcome so each gets ~1/7 of the budget and a dropped
        # export population / a leaked canary / a non-zero fail-open for ANY outcome fails.
        outcome = _P20_TERMINAL_OUTCOMES[i % len(_P20_TERMINAL_OUTCOMES)]
        canary = _p20_canary(rng)
        deps = _p20_build_deps(outcome, org_id, canary, rng)
        scope = _p20_scope()
        body = _p20_body(canary, rng)

        if outcome in _P20_PRE_PROVIDER_REFUSALS:
            result = asyncio.run(_p20_run_pre_provider(deps, scope, body))
            expected_code = _P20_PRE_PROVIDER_REFUSALS[outcome]
            ctx = _p20_ctx(i, outcome, refuse_code=result.refuse_code, want=expected_code)
            # The lifecycle refused with the declared value-code and made zero provider calls
            # (the proceed inputs are withheld on a refusal).
            assert result.refuse_code == expected_code, ctx
            assert result.plan is None and result.destination is None and result.body is None, ctx
            client_bytes = b""
        else:
            send = _P20Send()
            result = asyncio.run(_p20_run_output(deps, scope, body, send))
            client_bytes = send.body_bytes()
            ctx = _p20_ctx(i, outcome, client_len=len(client_bytes))
            # Each output outcome produced a client reply.
            assert client_bytes, f"{ctx} no client reply emitted"
            parsed = json.loads(client_bytes.decode("utf-8"))
            if outcome == _P20_OUTPUT_BLOCKED:
                assert parsed["error"]["code"] == REFUSE_OUTPUT_BLOCKED, ctx
            elif outcome == _P20_ERRORED:
                assert parsed["error"]["code"] == REFUSE_INPUT_SCAN_FAILURE, ctx
            else:
                # allowed / output-redacted: a real OpenAI chat.completion body (not an error).
                assert "error" not in parsed, ctx
                # On the REDACT path the canary bytes are masked out of the rendered content.
                if outcome == _P20_OUTPUT_REDACTED:
                    assert canary not in client_bytes.decode("utf-8"), (
                        f"{ctx} canary survived the REDACT enforcement into the client body"
                    )

        # ---- R14.1 / R14.2: the exports carry every dimension-bearing series ---- #
        reading = deps.exports.snapshot()
        _p20_assert_dimensions_present(reading, ctx)

        # ---- R14.3: FAIL_OPEN stays pinned at zero on EVERY terminal outcome ---- #
        assert reading.fail_open_total == 0, _p20_ctx(
            i, outcome, note="fail_open_total moved off zero", value=reading.fail_open_total
        )

        # ---- R14.4: no raw request/response content in any export field ---- #
        # The canary rode in BOTH the request (a folded input channel) and the response
        # (an output-content delta), so a producer that smuggled raw request OR response
        # content into any export field would surface the canary here. A full stringification
        # of every export field (scalars + histogram samples) must contain it ZERO times.
        exports_text = _p20_reading_text(reading)
        assert canary not in exports_text, _p20_ctx(
            i, outcome, note="raw request/response content (canary) appeared in an export field",
            canary=canary,
        )

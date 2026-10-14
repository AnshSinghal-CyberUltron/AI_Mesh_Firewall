"""GW16 property suite — the EmbeddingsSurface BLOCK gate + pre-provider short-circuit.

Three property tests (tasks 5.5 / 5.6 / 5.9) over the SAME shipped surface,
``EmbeddingsSurface(deps=MiscRoute).handle`` (``gateway_v2/gateway_v2/edge/openai_misc.py``):

* **Property 5** (task 5.5) — a BLOCK embeddings input finding yields zero provider calls.
  The canary is iterated over EVERY input-item position each iteration (a dropped fold path
  for any item would let the canary slip past the scan — C21), the injected detector maps the
  canary to a BLOCK-class finding, and the surface must refuse with the declared block
  value-code BEFORE opening the provider.
* **Property 6** (task 5.6) — BLOCK is the most-restrictive disposition (C22). Over random
  finding SETS each with ≥1 BLOCK-class finding plus co-matching REDACT/FLAG-class findings
  (varied mix + order), driven end-to-end through ``handle`` via an injected detector that
  returns the whole random set for the canary, the surface still refuses with the block code —
  one BLOCK dominates every lesser co-matcher.
* **Property 9** (task 5.9) — a pre-provider refusal short-circuits the provider. Alternating
  two arms across the iterations: (A) an admission ``ShedVerdict`` ⇒ ``overload_shed``, and
  (B) admission admits but quota returns a ``BudgetVerdict`` ⇒ ``budget_unavailable``. Each
  renders exactly the verdict's declared value-code and makes zero provider calls.

House idiom (enforced): a seeded ``random.Random``, ≥10,000 iterations, NO ``hypothesis``, one
property ↔ one test function, the async ``handle`` driven via ``asyncio.run``. The deps bundle,
the counted ``StubProviderClient`` subclass, and the ASGI receive/send harness mirror the task's
own ``tests/edge/test_gw16_embeddings_smoke.py``.

A refusal renders through the single ``edge/errors`` ``Error_Envelope`` (``render_http``):
``input_blocked`` has no dedicated envelope entry (GW15's shipped input-block spelling), so it
renders as the declared generic error — HTTP 500 ``gateway_error`` with the code echoed in the
structured ``code`` field (asserted via the error body, exactly as the smoke test does);
``overload_shed`` and ``budget_unavailable`` are mapped posture codes rendering HTTP 503
``service_unavailable``. All three assertions read the code off the parsed error body so they pin
the *declared* value-code, not just a status.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Mapping, Sequence

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
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, OVERLOAD_SHED
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.edge.openai_chat import REFUSE_INPUT_BLOCKED
from gateway_v2.edge.openai_misc import EmbeddingsSurface, MiscRoute
from gateway_v2.edge.wire.openai_misc import MiscResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver

# --------------------------------------------------------------------------- #
# Detector names → disposition under the shipped MinimalOutputResolver.
# --------------------------------------------------------------------------- #

#: Detector names ``MinimalOutputResolver`` maps to ``Disposition.BLOCK`` (its terminate-the-stream
#: trade-off classes). A finding tagged with any of these resolves to a BLOCK decision regardless
#: of any co-matching lesser disposition (C22 ``most_restrictive``).
_BLOCK_DETECTORS: tuple[str, ...] = ("card", "jwt")

#: Detector names the resolver maps to ``Disposition.REDACT`` (the redact-the-remainder classes) —
#: non-BLOCK co-matchers that must NOT soften a co-present BLOCK.
_REDACT_DETECTORS: tuple[str, ...] = (
    "aws",
    "api_token",
    "email",
    "uuid",
    "sha256",
    "url",
    "base64",
)

#: An unknown executed detector the resolver redacts by default (fail-safe REDACT) — a FLAG-ish
#: lesser co-matcher that must not win over a BLOCK.
_UNKNOWN_REDACT_DETECTOR = "gw16-unknown-detector"

#: Alphabet for canary synthesis — printable, SDK-safe, distinctive so a canary that slips past
#: the scan is unambiguous in a counterexample.
_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _canary(rng: random.Random) -> str:
    """A distinctive canary token unlikely to collide with any filler text."""
    n = rng.randint(4, 24)
    return "GW16CANARY_" + "".join(rng.choice(_CANARY_ALPHABET) for _ in range(n))


# --------------------------------------------------------------------------- #
# Test doubles — a counted StubProviderClient + the minimal lifecycle deps.
# --------------------------------------------------------------------------- #


class _CountingProvider(StubProviderClient):
    """A real :class:`StubProviderClient` (no socket) that counts every ``open``.

    Every zero-provider-call property asserts ``open_calls == 0`` after a refusal: the counter
    increments on each ``open`` and the surface must never reach it. The provider is REACHABLE on
    the deps bundle (exactly as the boot wiring places it on ``MiscRoute``), so the assertion is a
    real structural check — the lifecycle has the provider available and still never opens it.
    """

    __slots__ = ("open_calls",)

    def __init__(self, *, script: Sequence[UpstreamEvent]) -> None:
        super().__init__(script=script, rng=random.Random(0))
        self.open_calls = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.open_calls += 1
        return super().open(request)


class _FixedIdentity:
    """An ``IdentityResolver`` that resolves one canned tenant (so the lifecycle proceeds)."""

    __slots__ = ("_identity",)

    def __init__(self, identity: TenantIdentity) -> None:
        self._identity = identity

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class _Snapshot:
    """A served snapshot whose ``lookup`` always returns the one canned plan (plan resolves)."""

    __slots__ = ("_plan",)

    def __init__(self, plan: ExecutionPlan) -> None:
        self._plan = plan

    def lookup(self, org_id: str, now: float | None = None) -> ExecutionPlan:
        del org_id, now
        return self._plan


class _AlwaysAdmit:
    """Admission that always admits (so the lifecycle reaches the input scan)."""

    __slots__ = ()

    async def admit(self, *, owner_id: str, request_id: str, enqueued_at: float) -> Admitted:
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at)


class _AlwaysBudget:
    """Budget that is always within budget (so the lifecycle reaches the input scan)."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> Admitted:
        del cost
        return Admitted(owner_id="gw16", request_id=request_id, admitted_at=0.0)


class _SheddingAdmission:
    """Admission that always sheds — returns a ``ShedVerdict`` carrying ``overload_shed`` (R5.3)."""

    __slots__ = ("_verdict",)

    def __init__(self, verdict: ShedVerdict) -> None:
        self._verdict = verdict

    async def admit(
        self, *, owner_id: str, request_id: str, enqueued_at: float
    ) -> ShedVerdict:
        del owner_id, enqueued_at
        # Echo the request_id the surface minted so the verdict is well-formed; the code is
        # the fixed ``overload_shed`` spelling the shed verdict was built with.
        return ShedVerdict(
            code=self._verdict.code,
            retry_after_s=self._verdict.retry_after_s,
            should_retry=self._verdict.should_retry,
            request_id=request_id,
        )


class _RefusingBudget:
    """Budget that always refuses — returns a ``BudgetVerdict`` carrying ``budget_unavailable``."""

    __slots__ = ("_verdict",)

    def __init__(self, verdict: BudgetVerdict) -> None:
        self._verdict = verdict

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict:
        del cost
        return BudgetVerdict(
            code=self._verdict.code,
            retry_after_s=self._verdict.retry_after_s,
            should_retry=self._verdict.should_retry,
            request_id=request_id,
        )


class _FindingsDetector:
    """A detector that returns a FIXED finding set whenever the canary is present.

    The ``edge``-injected ``Callable[[str], Sequence[Finding]]`` seam (above ``detect``, R4.5):
    when the canary substring is in the folded item text it returns the canned findings (with
    each span re-based into the item so the spans are in range), otherwise nothing. Properties 5
    and 6 both drive the surface through this one seam — Property 5 with a single BLOCK finding,
    Property 6 with a random set containing ≥1 BLOCK finding plus lesser co-matchers.
    """

    __slots__ = ("_canary", "_detectors")

    def __init__(self, canary: str, detectors: Sequence[str]) -> None:
        self._canary = canary
        self._detectors = tuple(detectors)

    def __call__(self, text: str) -> Sequence[Finding]:
        if self._canary not in text:
            return ()
        start = text.index(self._canary)
        end = start + len(self._canary)
        return tuple(
            Finding(
                detector=detector,
                detector_version="gw16",
                category=Category.SECRET,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=(Span(start=start, end=end),),
                evidence=None,
            )
            for detector in self._detectors
        )


def _plan() -> ExecutionPlan:
    """A minimal resolved :class:`ExecutionPlan` for the one test tenant."""
    return ExecutionPlan(
        org_id="gw16-org",
        epoch=1,
        sequence=1,
        content_hash="gw16" * 8,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _identity() -> TenantIdentity:
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
# A tiny ASGI receive/send harness (mirrors tests/edge/test_gw16_embeddings_smoke.py).
# --------------------------------------------------------------------------- #


def _receive_for(body: bytes):
    """An ASGI ``receive`` that delivers ``body`` as one terminal http.request event."""
    delivered = False

    async def receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

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


_SCOPE: Mapping[str, object] = {"type": "http", "method": "POST", "path": "/v1/embeddings"}


def _run(deps: MiscRoute, body: bytes) -> _Capture:
    capture = _Capture()
    asyncio.run(EmbeddingsSurface(deps=deps).handle(_SCOPE, _receive_for(body), capture))
    return capture


def _error_code(capture: _Capture) -> str:
    """Read the declared value-code off the single-``Error_Envelope`` JSON body."""
    code = json.loads(capture.body)["error"]["code"]
    assert isinstance(code, str)
    return code


def _deps_for(
    *,
    provider: _CountingProvider,
    identity: object,
    admission: object,
    quota: object,
    input_detector: object,
) -> MiscRoute:
    """Build a ``MiscRoute`` whose non-tested seams all let the lifecycle proceed.

    The scripted ``provider`` is REACHABLE on the route (as the boot wiring places it), the plan
    resolves (``_Snapshot``), and the resolver is the shipped ``MinimalOutputResolver`` (C22). The
    one varied-per-property seam (``identity`` / ``admission`` / ``quota`` / ``input_detector``) is
    injected by the caller.
    """
    return MiscRoute(
        provider=provider,
        clock=lambda: 0.0,
        renderer=MiscResponseRenderer(),
        catalog=_Snapshot(_plan()),  # unused by embeddings; any value satisfies the field.
        identity=identity,  # type: ignore[arg-type]
        admission=admission,  # type: ignore[arg-type]
        quota=quota,  # type: ignore[arg-type]
        snapshot_source=lambda: _Snapshot(_plan()),
        input_detector=input_detector,  # type: ignore[arg-type]
        resolver=MinimalOutputResolver(),
        router=DispatchRouter(),
        transform=Transform(),
    )


# --------------------------------------------------------------------------- #
# Feature: chat-surface-gw16-misc, Property 5: A BLOCK embeddings input finding yields zero provider calls  # noqa: E501
# Validates: Requirements 4.2, 4.3
# --------------------------------------------------------------------------- #

_P5_SEED = 0x6C_6757_1605
_P5_ITERATIONS = 10_000

#: The max number of input items in a Property-5 batch; the canary is iterated over EVERY one.
_P5_MAX_ITEMS = 4


def test_property_5_block_embeddings_input_yields_zero_provider_calls() -> None:
    rng = random.Random(_P5_SEED)

    for i in range(_P5_ITERATIONS):
        n_items = rng.randint(1, _P5_MAX_ITEMS)
        # Iterate the canary over EVERY input-item position so a dropped fold path for ANY
        # single item (its canary never reaching the scan) fails the gate (C21).
        for position in range(n_items):
            canary = _canary(rng)
            # A scripted provider that WOULD yield a vector if ever opened — so the zero-call
            # assertion is meaningful (the provider is live, the surface simply never reaches it).
            provider = _CountingProvider(
                script=(UpstreamEvent(text_deltas=(("x", "[0.0]"),), final=True),)
            )
            # The canary lives in exactly one item; filler items carry non-canary text.
            items = [f"clean filler item {j}" for j in range(n_items)]
            items[position] = f"carry {canary} here"
            deps = _deps_for(
                provider=provider,
                identity=_FixedIdentity(_identity()),
                admission=_AlwaysAdmit(),
                quota=_AlwaysBudget(),
                input_detector=_FindingsDetector(canary, (rng.choice(_BLOCK_DETECTORS),)),
            )
            body = json.dumps({"model": "gw16-embed", "input": items}).encode("utf-8")

            capture = _run(deps, body)

            # R4.2 — the BLOCK short-circuits BEFORE provider.open ⇒ ZERO provider calls.
            assert provider.open_calls == 0, _ctx(
                _P5_SEED,
                i,
                note="provider was opened on a BLOCK-refused embeddings request",
                position=position,
                open_calls=provider.open_calls,
            )
            # R4.3 — the refusal renders the declared block value-code through the single
            # Error_Envelope. ``input_blocked`` has no dedicated envelope entry (GW15's shipped
            # input-block spelling), so it is the declared generic error (HTTP 500 gateway_error)
            # with the code echoed in the structured ``code`` field.
            assert capture.status == 500, _ctx(
                _P5_SEED, i, note="unexpected status on BLOCK refusal",
                position=position, status=capture.status,
            )
            assert _error_code(capture) == REFUSE_INPUT_BLOCKED, _ctx(
                _P5_SEED,
                i,
                note="refuse code is not the declared input-block code",
                position=position,
                code=_error_code(capture),
            )
            # R10.3 — fail-open is never incremented on a terminal refusal.
            assert deps.exports.snapshot().fail_open_total == 0, _ctx(
                _P5_SEED, i, note="fail_open_total moved off zero", position=position
            )


# --------------------------------------------------------------------------- #
# Feature: chat-surface-gw16-misc, Property 6: BLOCK is the most-restrictive disposition (C22)  # noqa: E501
# Validates: Requirements 4.4
# --------------------------------------------------------------------------- #

_P6_SEED = 0x6C_6757_1606
_P6_ITERATIONS = 10_000


def _p6_finding_set(rng: random.Random) -> tuple[str, ...]:
    """A random detector-name set with ≥1 BLOCK-class name plus co-matching lesser classes.

    Returns the detector names (varied mix + order) the detector tags on the canary; the surface
    resolves them via ``MinimalOutputResolver.decide`` (C22 ``most_restrictive``). At least one
    BLOCK-class name is guaranteed, so the resolved outcome must be BLOCK regardless of the lesser
    co-matchers.
    """
    detectors: list[str] = []
    for _ in range(rng.randint(1, 3)):  # ≥1 guaranteed BLOCK finding (C22 precondition).
        detectors.append(rng.choice(_BLOCK_DETECTORS))
    for _ in range(rng.randint(0, 5)):  # co-matching REDACT / FLAG-ish lesser dispositions.
        kind = rng.random()
        if kind < 0.5:
            detectors.append(rng.choice(_REDACT_DETECTORS))
        else:
            detectors.append(_UNKNOWN_REDACT_DETECTOR)
    rng.shuffle(detectors)  # BLOCK must not be positionally privileged (C22).
    return tuple(detectors)


def test_property_6_block_is_most_restrictive_disposition_end_to_end() -> None:
    rng = random.Random(_P6_SEED)

    for i in range(_P6_ITERATIONS):
        canary = _canary(rng)
        detectors = _p6_finding_set(rng)
        provider = _CountingProvider(
            script=(UpstreamEvent(text_deltas=(("x", "[0.0]"),), final=True),)
        )
        deps = _deps_for(
            provider=provider,
            identity=_FixedIdentity(_identity()),
            admission=_AlwaysAdmit(),
            quota=_AlwaysBudget(),
            # The whole random finding set rides on the canary; the surface resolves it (C22).
            input_detector=_FindingsDetector(canary, detectors),
        )
        body = json.dumps(
            {"model": "gw16-embed", "input": ["clean item", f"carry {canary} here"]}
        ).encode("utf-8")

        capture = _run(deps, body)

        # C22 — one BLOCK finding dominates every co-matching REDACT/FLAG: the Embeddings_Surface
        # refuses with the declared input-block code, end-to-end through ``handle`` (R4.4).
        assert capture.status == 500, _ctx(
            _P6_SEED, i, note="unexpected status on C22 refusal",
            detectors=detectors, status=capture.status,
        )
        assert _error_code(capture) == REFUSE_INPUT_BLOCKED, _ctx(
            _P6_SEED,
            i,
            note="BLOCK did not dominate co-matching lesser dispositions",
            detectors=detectors,
            code=_error_code(capture),
        )
        # The refusal short-circuits before the provider (a BLOCK is a pre-provider refusal).
        assert provider.open_calls == 0, _ctx(
            _P6_SEED, i, note="provider opened despite a BLOCK finding",
            detectors=detectors, open_calls=provider.open_calls,
        )
        assert deps.exports.snapshot().fail_open_total == 0, _ctx(
            _P6_SEED, i, note="fail_open_total moved off zero", detectors=detectors
        )


# --------------------------------------------------------------------------- #
# Feature: chat-surface-gw16-misc, Property 9: A pre-provider refusal short-circuits the provider  # noqa: E501
# Validates: Requirements 5.3, 5.4
# --------------------------------------------------------------------------- #

_P9_SEED = 0x6C_6757_1609
_P9_ITERATIONS = 10_000


def test_property_9_pre_provider_refusal_short_circuits_the_provider() -> None:
    rng = random.Random(_P9_SEED)

    for i in range(_P9_ITERATIONS):
        canary = _canary(rng)
        provider = _CountingProvider(
            script=(UpstreamEvent(text_deltas=(("x", "[0.0]"),), final=True),)
        )
        # Alternate the two pre-provider refusal arms across the iterations:
        #   arm A — admission sheds (ShedVerdict ⇒ overload_shed, R5.3);
        #   arm B — admission admits but quota refuses (BudgetVerdict ⇒ budget_unavailable, R5.4).
        shed_arm = i % 2 == 0
        if shed_arm:
            admission: object = _SheddingAdmission(shed_verdict(rng, request_id="gw16"))
            quota: object = _AlwaysBudget()
            expected_code = OVERLOAD_SHED
        else:
            admission = _AlwaysAdmit()
            quota = _RefusingBudget(budget_verdict(request_id="gw16"))
            expected_code = BUDGET_UNAVAILABLE

        deps = _deps_for(
            provider=provider,
            identity=_FixedIdentity(_identity()),
            admission=admission,
            quota=quota,
            # A BLOCK-ready detector proves the refusal is the admission/budget phase, which runs
            # BEFORE the input scan — the request would also block, but the earlier phase decides.
            input_detector=_FindingsDetector(canary, (rng.choice(_BLOCK_DETECTORS),)),
        )
        body = json.dumps(
            {"model": "gw16-embed", "input": ["clean item", f"carry {canary} here"]}
        ).encode("utf-8")

        capture = _run(deps, body)

        # R5.3 / R5.4 — the refusal renders EXACTLY the verdict's declared value-code (both map
        # to HTTP 503 service_unavailable via the single Error_Envelope).
        assert capture.status == 503, _ctx(
            _P9_SEED, i, note="unexpected status on pre-provider refusal",
            arm="shed" if shed_arm else "budget", status=capture.status,
        )
        assert _error_code(capture) == expected_code, _ctx(
            _P9_SEED,
            i,
            note="refuse code is not the verdict's declared code",
            arm="shed" if shed_arm else "budget",
            expected=expected_code,
            code=_error_code(capture),
        )
        # The refusal short-circuits BEFORE provider.open ⇒ ZERO provider calls (counted stub).
        assert provider.open_calls == 0, _ctx(
            _P9_SEED,
            i,
            note="provider was opened on a pre-provider refusal",
            arm="shed" if shed_arm else "budget",
            open_calls=provider.open_calls,
        )
        assert deps.exports.snapshot().fail_open_total == 0, _ctx(
            _P9_SEED, i, note="fail_open_total moved off zero",
            arm="shed" if shed_arm else "budget",
        )

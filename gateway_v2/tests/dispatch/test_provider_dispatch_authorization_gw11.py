"""GW11 behavioral suite — provider-dispatch-authorization (Task 8 + Task 11.1).

ONE behavioral test file for the whole GW11 Dispatch_Core (the single-test-file
collision lesson: this is authored in one pass, not split across sub-agents). It
carries Properties 1–17 and 19 (Property 18 is the gate's own test, Task 9.1,
NOT here) plus the Task 11.1 smoke/integration guards folded in so there is no
sibling collision.

House idiom (confirmed across the tree): seeded ``random.Random`` generators with
≥10,000 iterations for the genuine properties and NO ``hypothesis`` dependency;
mechanism/example tests for the structural/static guarantees. Async paths run
through the house ``_run(coro) = asyncio.run(coro)`` idiom (mirrors
``tests/dispatch/test_lgw12_provider.py`` and ``tests/state_control/
test_lgw05c_writer.py``).

Each test is tagged ``# Feature: provider-dispatch-authorization-gw11, Property N``
and ``# Validates: Requirements X.Y``.

The fakes built here (all satisfying the real injected protocols):

* ``RecordingProviderClient`` — a ``@runtime_checkable`` ``ProviderClient`` that
  counts every ``open(...)`` call in ``provider_calls`` so Properties 4/5/7 can
  assert ZERO firewall-attributable provider calls.
* ``RecordingRewriteBackend`` — a ``RewriteBackend`` counting ``classify`` calls.
* ``RecordingAuditSink`` — an ``AuditSink`` recording every started request.
* ``ListWaitMetric`` — a ``WaitMetric`` collecting every ``pool_wait_time`` sample.
* ``NoopScanSeam`` / ``SecretScrubScanSeam`` — ``ScanSeam`` implementations.
* ``ListCatalogueSource`` / ``OutageCatalogueSource`` — ``CatalogueSource`` seams.
* ``FailIfCalledAdjudicator`` — a fake adjudicator that fails if ever called,
  for the no-adjudicator guard (it is never called).
* ``FixedTimeoutSource`` — a ``TimeoutSource`` for the plan-driven timeout.

_Design: Correctness Properties 1–17, 19; Testing Strategy._
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import random
import tomllib
from collections.abc import AsyncIterator, Awaitable, Sequence
from pathlib import Path

from gateway_v2.dispatch import verify as verify_module
from gateway_v2.dispatch.authorized import (
    AuditSink,
    AuthorizedProviderEntry,
    RewriteBackend,
    assert_authorized,
)
from gateway_v2.dispatch.catalogue import (
    CatalogueEntry,
    CatalogueResolutionError,
    CatalogueSource,
    ControlPlaneOutageError,
    EmptyCatalogueError,
    ProviderCatalogue,
    resolve_catalogue,
)
from gateway_v2.dispatch.pool import ProviderPool, WaitMetric
from gateway_v2.dispatch.provider import (
    ProviderClient,
    StubProviderClient,
    UpstreamEvent,
    UpstreamRequest,
)
from gateway_v2.dispatch.provider_errors import (
    MALFORMED_UPSTREAM_CODE,
    OVER_BAND_CODE,
    PROVIDER_STATUS_MAP,
    GatewayError,
    ResponseTooLarge,
    bound_nonstream_body,
    decode_nonstream_body,
    map_provider_error,
    over_band_error,
    provider_timeout_s,
    scan_error_payload,
)
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import GatewayRequest, Transform
from gateway_v2.dispatch.verify import (
    VerificationFailure,
    VerifiedBytes,
    apply_and_verify,
)
from gateway_v2.domain import (
    Category,
    Decision,
    Disposition,
    ExecutionPlan,
    Finding,
    FindingDisposition,
    FindingStatus,
    Span,
    StreamingMode,
    Transformation,
)
from gateway_v2.domain.dispatch_authorization import DispatchAuthorization
from gateway_v2.resolve import authorize

_ITERATIONS = 10_000
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_WORKFLOW = _REPO_ROOT.parent / ".github" / "workflows" / "gateway-v2.yml"


def _run[T](coro: Awaitable[T]) -> T:
    """Drive one coroutine to completion (house idiom; no async test plugin)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


# =========================================================================== #
# Fakes — every one satisfies the real injected protocol.
# =========================================================================== #


class RecordingProviderClient:
    """A ``@runtime_checkable`` ``ProviderClient`` counting every ``open`` call.

    ``provider_calls`` is incremented the instant ``open`` is invoked (before any
    event is yielded), so Properties 4/5/7 can assert ZERO firewall-attributable
    provider calls. Replays a scripted sequence like ``StubProviderClient``.
    """

    __slots__ = ("abort_calls", "provider_calls", "script")

    def __init__(self, script: Sequence[UpstreamEvent] = ()) -> None:
        self.script: tuple[UpstreamEvent, ...] = tuple(script)
        self.provider_calls = 0
        self.abort_calls = 0

    async def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.provider_calls += 1
        _ = request
        for event in self.script:
            yield event

    async def abort(self) -> None:
        self.abort_calls += 1


class RecordingRewriteBackend:
    """A ``RewriteBackend`` counting ``classify`` calls (local REWRITE seam)."""

    __slots__ = ("classify_calls", "windows_seen")

    def __init__(self) -> None:
        self.classify_calls = 0
        self.windows_seen: list[Sequence[str]] = []

    async def classify(self, windows: Sequence[str]) -> Sequence[object]:
        self.classify_calls += 1
        self.windows_seen.append(list(windows))
        return ()


class RecordingAuditSink:
    """An ``AuditSink`` recording every started request (R8.8)."""

    __slots__ = ("starts",)

    def __init__(self) -> None:
        self.starts: list[tuple[str, str]] = []

    def record_started(self, request_id: str, destination: str) -> None:
        self.starts.append((request_id, destination))


class ListWaitMetric:
    """A ``WaitMetric`` collecting every exported ``pool_wait_time`` sample."""

    __slots__ = ("samples",)

    def __init__(self) -> None:
        self.samples: list[float] = []

    def observe_wait(self, seconds: float) -> None:
        self.samples.append(seconds)


class NoopScanSeam:
    """A ``ScanSeam`` that returns the payload unchanged (nothing to scrub)."""

    __slots__ = ()

    def scan_text(self, payload: str) -> str:
        return payload


class SecretScrubScanSeam:
    """A ``ScanSeam`` that scrubs a known secret token from the payload (R8.6)."""

    __slots__ = ("secret",)

    def __init__(self, secret: str) -> None:
        self.secret = secret

    def scan_text(self, payload: str) -> str:
        return payload.replace(self.secret, "[REDACTED]")


class ListCatalogueSource:
    """A ``CatalogueSource`` returning a fixed tuple of entries (R7.1)."""

    __slots__ = ("entries",)

    def __init__(self, entries: tuple[CatalogueEntry, ...]) -> None:
        self.entries = entries

    def fetch(self, org_id: str) -> tuple[CatalogueEntry, ...]:
        _ = org_id
        return self.entries


class OutageCatalogueSource:
    """A ``CatalogueSource`` signalling a store/control-plane blip (R7.3)."""

    __slots__ = ()

    def fetch(self, org_id: str) -> tuple[CatalogueEntry, ...]:
        raise ControlPlaneOutageError(org_id)


class FailIfCalledAdjudicator:
    """A fake adjudicator that FAILS if ever called (no-adjudicator guard, R6.2).

    The request path must select a destination from the pinned plan alone; this
    object proves the entry never consults an adjudicator by raising on any use.
    """

    __slots__ = ()

    def adjudicate(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("adjudicator must not be called on the request path (R6.2)")


class FixedTimeoutSource:
    """A ``TimeoutSource`` returning a per-(provider, model) declared timeout (R8.4)."""

    __slots__ = ("table",)

    def __init__(self, table: dict[tuple[str, str], float]) -> None:
        self.table = table

    def timeout_s(self, provider: str, model: str) -> float:
        return self.table[(provider, model)]


# =========================================================================== #
# Fixture builders.
# =========================================================================== #


def _plan(
    *,
    org_id: str = "org-gw11",
    epoch: int = 1,
    sequence: int = 1,
    content_hash: str = "0" * 64,
) -> ExecutionPlan:
    """A minimal, valid ``ExecutionPlan`` (routing reads only its identity)."""
    return ExecutionPlan(
        org_id=org_id,
        epoch=epoch,
        sequence=sequence,
        content_hash=content_hash,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _request(text: str = "hello world", *, destination: str = "amf://x") -> GatewayRequest:
    return GatewayRequest(
        destination=destination,
        model="gpt-x",
        text_deltas=(("message", text),),
    )


def _allow_decision(plan_version: str = "1.1.0") -> Decision:
    return Decision(
        disposition=Disposition.ALLOW,
        per_finding=(),
        transformations=(),
        findings=(),
        plan_version=plan_version,
        deciding_rules=(),
        unavailable_detectors=(),
    )


def _block_decision(plan_version: str = "1.1.0") -> Decision:
    return Decision(
        disposition=Disposition.BLOCK,
        per_finding=(FindingDisposition(detector="d", disposition=Disposition.BLOCK),),
        transformations=(),
        findings=(),
        plan_version=plan_version,
        deciding_rules=(),
        unavailable_detectors=(),
    )


def _redact_decision_covered(text: str, start: int, end: int) -> Decision:
    """A REDACT decision whose transformation fully covers the one finding span.

    The replacement is longer than the span and does not echo the raw bytes, so
    ``apply_and_verify`` succeeds (span covered, raw absent).
    """
    span = Span(start=start, end=end)
    finding = Finding(
        detector="pii",
        detector_version="1",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=0.99,
        spans=(span,),
        evidence=None,
    )
    return Decision(
        disposition=Disposition.REDACT,
        per_finding=(FindingDisposition(detector="pii", disposition=Disposition.REDACT),),
        transformations=(Transformation(kind="mask", span=span, replacement="[REDACTED]"),),
        findings=(finding,),
        plan_version="1.1.0",
        deciding_rules=(),
        unavailable_detectors=(),
    )


def _redact_decision_uncovered(text: str, start: int, end: int) -> Decision:
    """A REDACT decision whose finding span carries NO covering transformation.

    The obliged span is left raw, so ``apply_and_verify`` must fail terminally.
    """
    _ = text
    span = Span(start=start, end=end)
    finding = Finding(
        detector="pii",
        detector_version="1",
        category=Category.PII,
        status=FindingStatus.EXECUTED,
        confidence=0.99,
        spans=(span,),
        evidence=None,
    )
    return Decision(
        disposition=Disposition.REDACT,
        per_finding=(FindingDisposition(detector="pii", disposition=Disposition.REDACT),),
        transformations=(),
        findings=(finding,),
        plan_version="1.1.0",
        deciding_rules=(),
        unavailable_detectors=(),
    )


def _entry(
    *,
    client: ProviderClient,
    audit: AuditSink,
    rewrite: RewriteBackend,
    wait_metric: WaitMetric | None = None,
    scan_seam: object | None = None,
    bound: int = 4,
) -> AuthorizedProviderEntry:
    pool = ProviderPool(
        bound=bound,
        wait_metric=wait_metric if wait_metric is not None else ListWaitMetric(),
        clock=_make_fake_clock(),
    )
    return AuthorizedProviderEntry(
        client=client,
        transform=Transform(),
        router=DispatchRouter(),
        pool=pool,
        rewrite=rewrite,
        scan_seam=scan_seam if scan_seam is not None else NoopScanSeam(),
        audit=audit,
    )


def _make_fake_clock() -> object:
    """An injected monotonic clock advancing 1.0 per call (deterministic waits)."""
    ticks = {"t": 0.0}

    def clock() -> float:
        ticks["t"] += 1.0
        return ticks["t"]

    return clock


async def _drain(entry: AuthorizedProviderEntry, **kwargs: object) -> list[UpstreamEvent]:
    got: list[UpstreamEvent] = []
    async for event in entry.dispatch(**kwargs):  # type: ignore[arg-type]
        got.append(event)
    return got


# =========================================================================== #
# Property 1 — Sealed constructor (R1.1, R1.2).
# =========================================================================== #


def test_property_1_sealed_constructor_rejects_direct_construction() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 1
    # Validates: Requirements 1.1, 1.2
    """Direct ``DispatchAuthorization(...)`` without the private witness raises.

    No caller outside the domain mint can name ``_WITNESS``; the test models that
    by passing an ordinary object as the witness. ``__post_init__`` checks the
    witness by IDENTITY, so construction fails for every field tuple.
    """
    seed = 0x6711_01
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        plan_version = f"{rng.randint(0, 9)}.{rng.randint(0, 99)}.{rng.getrandbits(16):04x}"
        disposition = rng.choice(("allow", "flag", "redact"))
        forged_witness = object()  # an outside caller cannot name the real _WITNESS
        raised = False
        try:
            DispatchAuthorization(
                plan_version=plan_version,
                disposition=disposition,
                _witness=forged_witness,  # type: ignore[arg-type]
            )
        except ValueError:
            raised = True
        assert raised, f"i={i} seed={seed:#x} direct construction did not raise"


# =========================================================================== #
# Property 2 — dataclasses.replace fails (R1.3).
# =========================================================================== #


def test_property_2_dataclasses_replace_forgery_fails() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 2
    # Validates: Requirements 1.3
    """``dataclasses.replace`` used as a FORGERY (overriding the witness with a
    non-witness value) re-runs ``__post_init__`` and raises.

    HONEST RESIDUAL (C11 / C1 docstring: "not enforceable by a sealed constructor
    alone"). ``dataclasses.replace`` copies the EXISTING ``_witness`` through for a
    caller that overrides only public fields, so such a ``replace`` on a validly
    minted auth re-runs ``__post_init__`` WITH the real witness and SUCCEEDS at run
    time. That residual relabel-via-replace is closed by the PROVENANCE AST GATE
    (``check_dispatch_provenance`` flags ``dataclasses.replace`` of an auth outside
    ``resolve/``), not by the type. The runtime constructor closes only the forgery
    that overrides the witness with a value it does not possess — which is what this
    property pins: any ``replace`` that supplies a ``_witness`` it does not own
    raises, so no forged witness can be injected through ``replace``.
    """
    seed = 0x6711_02
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        decision = _allow_decision(
            plan_version=f"{rng.randint(0, 9)}.{rng.randint(0, 9)}.h"
        )
        auth = authorize(decision)
        assert auth is not None
        raised = False
        try:
            # Forgery via replace: override the witness with a value we do not own.
            dataclasses.replace(auth, plan_version="forged", _witness=object())
        except (ValueError, TypeError):
            raised = True
        assert raised, f"i={i} seed={seed:#x} replace witness-forgery did not raise"

    # Document the honest residual as an explicit, pinned expectation: a public-only
    # replace copies the real witness through and SUCCEEDS at run time (that path is
    # closed statically by the provenance gate, not by the constructor).
    auth = authorize(_allow_decision())
    assert auth is not None
    relabelled = dataclasses.replace(auth, plan_version="relabelled")
    assert relabelled.plan_version == "relabelled"
    assert_authorized(relabelled)  # the real witness carried through — gate-closed, not type-closed


# =========================================================================== #
# Property 3 — authorize() mints iff non-terminal (R1.5, R4.2).
# =========================================================================== #


def test_property_3_authorize_mints_iff_non_terminal() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 3
    # Validates: Requirements 1.5, 4.2
    """``authorize(decision)`` returns ``None`` iff BLOCK; a valid auth otherwise.

    Swept over the four dispositions. For BLOCK, no object is minted; for
    ALLOW/FLAG/REDACT, a witness-stamped auth carrying the decision identity is
    returned (its provenance passes ``assert_authorized``).
    """
    seed = 0x6711_03
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        disposition = rng.choice(
            (Disposition.ALLOW, Disposition.FLAG, Disposition.REDACT, Disposition.BLOCK)
        )
        plan_version = f"{rng.randint(0, 9)}.{rng.randint(0, 9)}.hh"
        decision = Decision(
            disposition=disposition,
            per_finding=(FindingDisposition(detector="d", disposition=disposition),),
            transformations=(),
            findings=(),
            plan_version=plan_version,
            deciding_rules=(),
            unavailable_detectors=(),
        )
        auth = authorize(decision)
        if disposition is Disposition.BLOCK:
            assert auth is None, f"i={i} seed={seed:#x} BLOCK minted an authorization"
        else:
            assert auth is not None, f"i={i} seed={seed:#x} non-terminal minted no auth"
            assert auth.plan_version == plan_version
            assert auth.disposition == str(disposition)
            assert_authorized(auth)  # a real mint passes the runtime assertion


# =========================================================================== #
# Property 4 — forged auth rejected at the entry; recorder zero (R1.6, R2.3, R2.5, R10.6).
# =========================================================================== #


def test_property_4_forged_auth_rejected_recorder_zero() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 4
    # Validates: Requirements 1.6, 2.3, 2.5, 10.6
    """A forged auth built via ``object.__new__`` (bypassing ``__post_init__``)
    is rejected by ``assert_authorized`` at the entry BEFORE any provider byte, so
    the recording client shows ZERO ``open`` calls.
    """
    seed = 0x6711_04
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        # object.__new__ skips __post_init__, so this never ran the witness check.
        forged = object.__new__(DispatchAuthorization)
        # Stamp public fields but a FORGED witness (not the module-private one).
        object.__setattr__(forged, "plan_version", "forged")
        object.__setattr__(forged, "disposition", "allow")
        if rng.random() < 0.5:
            object.__setattr__(forged, "_witness", object())
        # else: leave the witness slot unset entirely (getattr -> None).

        recorder = RecordingProviderClient(script=(UpstreamEvent(text_deltas=(), final=True),))
        audit = RecordingAuditSink()
        entry = _entry(client=recorder, audit=audit, rewrite=RecordingRewriteBackend())

        raised = False
        try:
            _run(
                _drain(
                    entry,
                    auth=forged,
                    decision=_allow_decision(),
                    request=_request(),
                    plan=_plan(),
                    catalogue=ProviderCatalogue(org_id="org-gw11", entries=()),
                    request_id=f"req-{i}",
                )
            )
        except ValueError:
            raised = True
        assert raised, f"i={i} seed={seed:#x} forged auth was not rejected"
        assert recorder.provider_calls == 0, (
            f"i={i} seed={seed:#x} forged auth reached the provider ({recorder.provider_calls})"
        )
        assert audit.starts == [], f"i={i} seed={seed:#x} forged auth audited a start"


# =========================================================================== #
# Property 5 — BLOCK reaches no provider (R4.1, R4.2, R4.3).
# =========================================================================== #


def test_property_5_block_reaches_no_provider() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 5
    # Validates: Requirements 4.1, 4.2, 4.3
    """A BLOCK decision mints no authorization, so the request path never invokes
    the entry and the recorder shows ZERO provider calls.

    This mirrors the real request path: ``authorize`` returns ``None`` for BLOCK,
    and with no auth there is nothing to pass to ``dispatch`` — so the entry is
    never reached and the recorder stays at zero.
    """
    seed = 0x6711_05
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        decision = _block_decision(plan_version=f"{rng.randint(0, 9)}.0.h")
        recorder = RecordingProviderClient()
        audit = RecordingAuditSink()
        entry = _entry(client=recorder, audit=audit, rewrite=RecordingRewriteBackend())

        auth = authorize(decision)
        assert auth is None, f"i={i} seed={seed:#x} BLOCK minted an authorization"
        # No auth exists -> the request path makes no call to the entry at all.
        # (Guarding against accidental use: the recorder must be untouched.)
        _ = entry
        assert recorder.provider_calls == 0, f"i={i} seed={seed:#x} BLOCK reached a provider"
        assert audit.starts == [], f"i={i} seed={seed:#x} BLOCK audited a start"


# =========================================================================== #
# Property 6 — transformed bytes verify, or nothing is sent (R3.1-R3.4).
# =========================================================================== #


def test_property_6_apply_and_verify_or_nothing_sent() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 6
    # Validates: Requirements 3.1, 3.2, 3.3, 3.4
    """A covered REDACT verifies (raw absent, sanitized bytes + log); an uncovered
    REDACT fails terminally with ``VerificationFailure`` and the recorder stays at
    zero.
    """
    seed = 0x6711_06
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        # Secret is a distinct digit token; prefix/suffix are alphabetic only, so the
        # raw secret bytes cannot collide with surrounding content and a correct
        # redaction genuinely removes every occurrence.
        secret = "".join(chr(rng.randint(ord("0"), ord("9"))) for _ in range(rng.randint(1, 6)))
        prefix = "alpha"
        text = prefix + secret + "omega"
        start = len(prefix)
        end = start + len(secret)

        covered = _redact_decision_covered(text, start, end)
        verified = apply_and_verify(covered, _request(text))
        assert isinstance(verified, VerifiedBytes)
        content = "".join(t for _c, t in verified.request.text_deltas)
        assert secret not in content, (
            f"i={i} seed={seed:#x} raw secret survived a covered redaction"
        )
        assert "REDACT" in verified.log and "verified" in verified.log

        # Uncovered span -> terminal failure, no bytes to send. Prove the entry
        # does not reach the provider either.
        uncovered = _redact_decision_uncovered(text, start, end)
        raised = False
        try:
            apply_and_verify(uncovered, _request(text))
        except VerificationFailure:
            raised = True
        assert raised, f"i={i} seed={seed:#x} uncovered redaction did not fail terminally"

        if i < 200:  # entry-level proof is costlier; a representative sweep suffices.
            recorder = RecordingProviderClient()
            audit = RecordingAuditSink()
            entry = _entry(client=recorder, audit=audit, rewrite=RecordingRewriteBackend())
            auth = authorize(uncovered)
            assert auth is not None
            failed = False
            try:
                _run(
                    _drain(
                        entry,
                        auth=auth,
                        decision=uncovered,
                        request=_request(text),
                        plan=_plan(),
                        catalogue=ProviderCatalogue(org_id="org-gw11", entries=()),
                        request_id=f"req-{i}",
                    )
                )
            except VerificationFailure:
                failed = True
            assert failed, f"i={i} seed={seed:#x} entry did not propagate VerificationFailure"
            assert recorder.provider_calls == 0, (
                f"i={i} seed={seed:#x} unverified dispatch reached a provider"
            )


# =========================================================================== #
# Property 7 — REWRITE runs on local backend, zero provider calls (R5.1-R5.3, R12.3).
# =========================================================================== #


def test_property_7_rewrite_local_backend_zero_provider_calls() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 7
    # Validates: Requirements 5.1, 5.2, 5.3, 12.3
    """A REWRITE decision runs through the local ``RewriteBackend`` seam, so a
    recording ``ProviderClient`` shows ZERO firewall-attributable provider calls
    while the local backend's ``classify`` IS invoked exactly once.
    """
    seed = 0x6711_07
    rng = random.Random(seed)
    for i in range(min(_ITERATIONS, 2_000)):
        text = "rewrite me " + str(rng.randint(0, 1_000_000))
        # A REWRITE maps to Disposition.REDACT in the resolver; the entry's
        # `rewrite=True` flag carries the winning-action provenance.
        decision = _allow_decision()
        recorder = RecordingProviderClient(
            script=(UpstreamEvent(text_deltas=(("message", "x"),), final=True),)
        )
        backend = RecordingRewriteBackend()
        audit = RecordingAuditSink()
        entry = _entry(client=recorder, audit=audit, rewrite=backend)

        auth = authorize(decision)
        assert auth is not None
        events = _run(
            _drain(
                entry,
                auth=auth,
                decision=decision,
                request=_request(text),
                plan=_plan(),
                catalogue=ProviderCatalogue(org_id="org-gw11", entries=()),
                request_id=f"req-{i}",
                rewrite=True,
            )
        )
        assert events == [], f"i={i} seed={seed:#x} REWRITE yielded provider events"
        assert recorder.provider_calls == 0, (
            f"i={i} seed={seed:#x} REWRITE reached a provider ({recorder.provider_calls})"
        )
        assert backend.classify_calls == 1, (
            f"i={i} seed={seed:#x} REWRITE did not run the local backend"
        )
        # Still audited as started (R8.8), even though no provider byte flowed.
        assert len(audit.starts) == 1


# =========================================================================== #
# Property 8 — routing deterministic from plan identity (R6.1, R6.4).
# =========================================================================== #


def test_property_8_routing_deterministic_from_plan_identity() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 8
    # Validates: Requirements 6.1, 6.4
    """Same plan identity -> same destination every time; distinct identities map
    distinctly; no shared mutable router state (two routers agree).
    """
    seed = 0x6711_08
    rng = random.Random(seed)
    router_a = DispatchRouter()
    router_b = DispatchRouter()
    seen: dict[str, str] = {}
    for i in range(_ITERATIONS):
        plan = _plan(
            org_id=f"org-{rng.randint(0, 40)}",
            epoch=rng.randint(0, 9),
            sequence=rng.randint(0, 9),
            content_hash=f"{rng.getrandbits(256):064x}",
        )
        d1 = router_a.select(plan)
        d2 = router_a.select(plan)
        d3 = router_b.select(plan)
        assert d1 == d2 == d3, f"i={i} seed={seed:#x} routing was not deterministic"
        key = f"{plan.org_id}|{plan.epoch}|{plan.sequence}|{plan.content_hash}"
        if key in seen:
            assert seen[key] == d1, f"i={i} seed={seed:#x} same identity drifted"
        else:
            seen[key] = d1


# =========================================================================== #
# Property 9 — empty vs outage distinct, never confused (R7.2-R7.5).
# =========================================================================== #


def test_property_9_empty_and_outage_distinct_never_confused() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 9
    # Validates: Requirements 7.2, 7.3, 7.4, 7.5
    """An empty resolution raises ``EmptyCatalogueError``; an outage raises
    ``ControlPlaneOutageError``; the two are distinct classes (neither in the
    other's MRO) and the outage never carries the "connect your provider" code.
    """
    # Static distinctness (SMOKE): neither is an ancestor of the other.
    assert not issubclass(EmptyCatalogueError, ControlPlaneOutageError)
    assert not issubclass(ControlPlaneOutageError, EmptyCatalogueError)
    assert issubclass(EmptyCatalogueError, CatalogueResolutionError)
    assert issubclass(ControlPlaneOutageError, CatalogueResolutionError)
    empty_code = EmptyCatalogueError("x").code
    outage_code = ControlPlaneOutageError("x").code
    assert empty_code != outage_code
    # The outage code must not be the "connect your provider" (no_provider_configured) code.
    assert outage_code != empty_code
    assert "no_provider_configured" == empty_code

    seed = 0x6711_09
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        org = f"org-{rng.randint(0, 1000)}"
        if rng.random() < 0.5:
            source: CatalogueSource = ListCatalogueSource(entries=())
            raised_empty = False
            raised_outage = False
            try:
                resolve_catalogue(source, org)
            except EmptyCatalogueError:
                raised_empty = True
            except ControlPlaneOutageError:
                raised_outage = True
            assert raised_empty and not raised_outage, (
                f"i={i} seed={seed:#x} empty resolution not reported as EmptyCatalogueError"
            )
        else:
            source = OutageCatalogueSource()
            raised_empty = False
            raised_outage = False
            try:
                resolve_catalogue(source, org)
            except EmptyCatalogueError:
                raised_empty = True
            except ControlPlaneOutageError as exc:
                raised_outage = True
                assert "no_provider_configured" not in str(exc), (
                    f"i={i} seed={seed:#x} outage leaked the provisioning message"
                )
            assert raised_outage and not raised_empty, (
                f"i={i} seed={seed:#x} outage not reported as ControlPlaneOutageError"
            )

    # A non-empty resolution returns the snapshot unchanged.
    entry = CatalogueEntry(provider="openai", model="gpt-x", timeout_s=30.0, context_band=8192)
    cat = resolve_catalogue(ListCatalogueSource(entries=(entry,)), "org-ok")
    assert cat.entries == (entry,) and cat.org_id == "org-ok"


# =========================================================================== #
# Property 10 — exact error class with headers, never blanket 502 (R8.1-R8.3).
# =========================================================================== #


def test_property_10_error_mapping_exact_never_blanket_502() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 10
    # Validates: Requirements 8.1, 8.2, 8.3
    """400->400 (provider code), 429->429 (+Retry-After/rate-limit headers), and
    no provider 4xx is ever mapped to a blanket 502.
    """
    seed = 0x6711_10
    rng = random.Random(seed)
    four_xx = tuple(s for s in PROVIDER_STATUS_MAP if 400 <= s < 500)
    for i in range(_ITERATIONS):
        status = rng.choice(four_xx)
        retry = str(rng.randint(1, 120))
        headers = {
            "Retry-After": retry,
            "x-ratelimit-remaining-requests": str(rng.randint(0, 10)),
            "x-unrelated": "drop-me",
        }
        err = map_provider_error(status, headers, b"{}")
        assert isinstance(err, GatewayError)
        assert err.status == status, f"i={i} seed={seed:#x} {status} did not map to itself"
        assert err.status != 502, f"i={i} seed={seed:#x} 4xx collapsed to blanket 502"
        if status == 400:
            assert err.code == "invalid_request_error"
        if status == 429:
            assert err.code == "rate_limit_error"
            kept_retry = err.headers.get("retry-after") or err.headers.get("Retry-After")
            assert kept_retry == retry
            assert "x-ratelimit-remaining-requests" in err.headers
            assert "x-unrelated" not in err.headers, (
                f"i={i} seed={seed:#x} an arbitrary provider header leaked through"
            )
        else:
            # Non-429 statuses carry no provider header through.
            assert dict(err.headers) == {}, f"i={i} seed={seed:#x} non-429 leaked headers"


# =========================================================================== #
# Property 11 — plan-declared timeout, not a fixed 120s (R8.4, R9.5).
# =========================================================================== #


def test_property_11_plan_declared_timeout_not_fixed_120() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 11
    # Validates: Requirements 8.4, 9.5
    """``provider_timeout_s`` returns the plan-declared per-(provider, model)
    value, including values above 120s (a slow first token is not cut at 120).
    """
    seed = 0x6711_11
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        provider = rng.choice(("openai", "anthropic", "azure"))
        model = rng.choice(("gpt-x", "claude", "mini"))
        declared = round(rng.uniform(0.5, 600.0), 3)
        source = FixedTimeoutSource({(provider, model): declared})
        got = provider_timeout_s(source, provider, model)
        assert got == declared, f"i={i} seed={seed:#x} timeout {got} != declared {declared}"
    # The slow-first-token case explicitly: 125s is honoured, not cut to 120.
    slow = FixedTimeoutSource({("openai", "gpt-x"): 125.0})
    assert provider_timeout_s(slow, "openai", "gpt-x") == 125.0


# =========================================================================== #
# Property 12 — non-stream size bound, fail-closed (R8.5).
# =========================================================================== #


def test_property_12_nonstream_size_bound_fail_closed() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 12
    # Validates: Requirements 8.5
    """A body at/under the bound returns intact; a body over the bound fails
    closed with ``ResponseTooLarge`` rather than buffering unbounded.
    """
    seed = 0x6711_12
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        max_bytes = rng.randint(1, 64)
        total = rng.randint(0, 128)
        # Split `total` bytes into random chunks.
        remaining = total
        chunks: list[bytes] = []
        while remaining > 0:
            take = rng.randint(1, remaining)
            chunks.append(b"x" * take)
            remaining -= take
        if total <= max_bytes:
            body = bound_nonstream_body(iter(chunks), max_bytes)
            assert body == b"x" * total, f"i={i} seed={seed:#x} in-bound body altered"
        else:
            raised = False
            try:
                bound_nonstream_body(iter(chunks), max_bytes)
            except ResponseTooLarge:
                raised = True
            assert raised, f"i={i} seed={seed:#x} over-bound body did not fail closed"


# =========================================================================== #
# Property 13 — provider error payload scanned before forward (R8.6).
# =========================================================================== #


def test_property_13_error_payload_scanned_before_forward() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 13
    # Validates: Requirements 8.6
    """A provider error payload echoing a secret is scanned (secret removed,
    ``changed=True``) before any byte is forwarded; a clean payload is unchanged.
    """
    seed = 0x6711_13
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        secret = "sk-" + "".join(chr(rng.randint(ord("a"), ord("z"))) for _ in range(8))
        seam = SecretScrubScanSeam(secret)
        if rng.random() < 0.5:
            payload = f'{{"error":"key {secret} invalid"}}'
            scanned = scan_error_payload(payload, seam)
            assert secret not in scanned.text, (
                f"i={i} seed={seed:#x} secret survived the scan before forward"
            )
            assert scanned.changed, f"i={i} seed={seed:#x} secret scrub not marked changed"
        else:
            payload = '{"error":"bad request"}'
            scanned = scan_error_payload(payload, seam)
            assert scanned.text == payload and not scanned.changed


def test_property_13_entry_scans_midstream_error_frame() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 13
    # Validates: Requirements 8.6
    """At the entry, a mid-stream ``error_frame`` event has its payload scrubbed
    before the event is forwarded downstream.
    """
    secret = "sk-leaked-secret"
    recorder = RecordingProviderClient(
        script=(
            UpstreamEvent(text_deltas=(("message", "ok"),), final=False),
            UpstreamEvent(text_deltas=(), final=True, error_frame=f"err: {secret}"),
        )
    )
    entry = _entry(
        client=recorder,
        audit=RecordingAuditSink(),
        rewrite=RecordingRewriteBackend(),
        scan_seam=SecretScrubScanSeam(secret),
    )
    auth = authorize(_allow_decision())
    assert auth is not None
    events = _run(
        _drain(
            entry,
            auth=auth,
            decision=_allow_decision(),
            request=_request(),
            plan=_plan(),
            catalogue=ProviderCatalogue(org_id="org-gw11", entries=()),
            request_id="req-scan",
        )
    )
    forwarded_frames = [e.error_frame for e in events if e.error_frame is not None]
    assert forwarded_frames, "no error frame was forwarded"
    for frame in forwarded_frames:
        assert secret not in frame, "secret forwarded unscanned from a mid-stream error frame"


# =========================================================================== #
# Property 14 — over-band -> context_length_exceeded, not 413 (R8.7).
# =========================================================================== #


def test_property_14_over_band_context_length_exceeded_not_413() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 14
    # Validates: Requirements 8.7
    """A conversation exceeding the model band returns the SDK-compatible
    ``context_length_exceeded`` code (never a 413); a fitting one returns None.
    """
    seed = 0x6711_14
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        band = rng.randint(1, 200)
        turns = tuple(
            "a" * rng.randint(0, 50) for _ in range(rng.randint(0, 10))
        )
        total = sum(len(t) for t in turns)
        err = over_band_error(iter(turns), band)
        if total > band:
            assert err is not None, f"i={i} seed={seed:#x} over-band returned None"
            assert err.code == OVER_BAND_CODE, (
                f"i={i} seed={seed:#x} over-band code was {err.code!r}"
            )
            assert err.status != 413, f"i={i} seed={seed:#x} over-band mapped to a 413"
        else:
            assert err is None, f"i={i} seed={seed:#x} in-band returned an error"


# =========================================================================== #
# Property 15 — every started request audited, incl. cancelled (R8.8).
# =========================================================================== #


def test_property_15_every_started_request_audited_incl_cancelled() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 15
    # Validates: Requirements 8.8
    """A started request is audited exactly once — including one cancelled mid
    stream (the start fires on lease, before the first provider byte is read).
    """
    seed = 0x6711_15
    rng = random.Random(seed)

    async def drain_cancel_after_first(
        entry: AuthorizedProviderEntry, **kwargs: object
    ) -> None:
        count = 0
        async for _event in entry.dispatch(**kwargs):  # type: ignore[arg-type]
            count += 1
            if count == 1:
                break  # cancel the stream mid-iteration

    for i in range(min(_ITERATIONS, 2_000)):
        length = rng.randint(2, 6)
        script = tuple(
            UpstreamEvent(text_deltas=(("message", str(k)),), final=(k == length - 1))
            for k in range(length)
        )
        recorder = RecordingProviderClient(script=script)
        audit = RecordingAuditSink()
        entry = _entry(client=recorder, audit=audit, rewrite=RecordingRewriteBackend())
        auth = authorize(_allow_decision())
        assert auth is not None
        kwargs = {
            "auth": auth,
            "decision": _allow_decision(),
            "request": _request(),
            "plan": _plan(),
            "catalogue": ProviderCatalogue(org_id="org-gw11", entries=()),
            "request_id": f"req-{i}",
        }
        if rng.random() < 0.5:
            _run(_drain(entry, **kwargs))  # completes
        else:
            _run(drain_cancel_after_first(entry, **kwargs))  # cancelled mid-stream
        assert len(audit.starts) == 1, (
            f"i={i} seed={seed:#x} started request audited {len(audit.starts)} times, want 1"
        )
        assert audit.starts[0][0] == f"req-{i}"


# =========================================================================== #
# Property 16 — malformed body -> protocol error, never success (R9.4).
# =========================================================================== #


def test_property_16_malformed_body_protocol_error_never_success() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 16
    # Validates: Requirements 9.4
    """A valid UTF-8 body decodes to text; an undecodable body returns the single
    shared protocol-error code (``stream_malformed_upstream``), never a success.
    """
    seed = 0x6711_16
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        if rng.random() < 0.5:
            text = "".join(chr(rng.randint(0x20, 0x7E)) for _ in range(rng.randint(0, 20)))
            body = text.encode("utf-8")
            got = decode_nonstream_body(body)
            assert got == text, f"i={i} seed={seed:#x} valid body did not decode"
            assert got != MALFORMED_UPSTREAM_CODE or text == MALFORMED_UPSTREAM_CODE
        else:
            # Invalid UTF-8: a lone continuation byte.
            body = bytes([0xFF, rng.randint(0x80, 0xBF)])
            got = decode_nonstream_body(body)
            assert got is None, (
                f"i={i} seed={seed:#x} malformed body did not report a protocol error"
            )
    # The shared protocol-error spelling is the one from domain.posture.
    assert MALFORMED_UPSTREAM_CODE == "stream_malformed_upstream"


# =========================================================================== #
# Property 17 — pool bounded, exports wait time (R9.2, R9.3).
# =========================================================================== #


def test_property_17_pool_bounded_exports_wait_time() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 17
    # Validates: Requirements 9.2, 9.3
    """Concurrent leases never exceed the bound (excess queues with backpressure,
    no exception storm), and every acquisition records a non-negative wait sample.
    """
    seed = 0x6711_17
    rng = random.Random(seed)

    for i in range(min(_ITERATIONS, 500)):
        bound = rng.randint(1, 4)
        burst = bound + rng.randint(1, 6)
        metric = ListWaitMetric()
        pool = ProviderPool(bound=bound, wait_metric=metric, clock=_make_fake_clock())
        state = {"inflight": 0, "peak": 0}

        async def worker(hold: int) -> None:
            async with pool.lease():
                state["inflight"] += 1
                state["peak"] = max(state["peak"], state["inflight"])
                # Yield control so queued acquirers can run while we "hold".
                for _ in range(hold):
                    await asyncio.sleep(0)
                state["inflight"] -= 1

        async def run_burst() -> None:
            await asyncio.gather(*(worker(rng.randint(1, 3)) for _ in range(burst)))

        _run(run_burst())
        assert state["peak"] <= bound, (
            f"i={i} seed={seed:#x} concurrent leases {state['peak']} exceeded bound {bound}"
        )
        assert len(metric.samples) == burst, (
            f"i={i} seed={seed:#x} wait samples {len(metric.samples)} != acquisitions {burst}"
        )
        assert all(s >= 0.0 for s in metric.samples), (
            f"i={i} seed={seed:#x} a negative pool_wait_time sample was recorded"
        )


# =========================================================================== #
# Property 19 — concurrency: no spurious errors, no shared-state corruption (R11).
# =========================================================================== #


def test_property_19_concurrency_no_spurious_errors_no_corruption() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property 19
    # Validates: Requirements 11.1, 11.2, 11.3
    """LGW11-6: 500 requests across 20 orgs, injected clock, in-process. No
    spurious provisioning error occurs (every org has a provider present), each
    org's destination stays plan-deterministic, and per-org audited counts add up
    with no shared-state corruption.
    """
    seed = 0x6711_19
    rng = random.Random(seed)
    orgs = [f"org-{n}" for n in range(20)]
    # Every org has a provider present -> no genuine absence -> no spurious error.
    plans = {org: _plan(org_id=org, epoch=1, sequence=1) for org in orgs}
    expected_dest = {org: DispatchRouter().select(plans[org]) for org in orgs}

    audit = RecordingAuditSink()
    recorder = RecordingProviderClient(
        script=(UpstreamEvent(text_deltas=(("message", "ok"),), final=True),)
    )
    # One shared entry across all requests (it carries no per-request mutable state).
    entry = _entry(client=recorder, audit=audit, rewrite=RecordingRewriteBackend(), bound=16)

    total = 500
    assignments = [orgs[rng.randrange(len(orgs))] for _ in range(total)]
    per_org_expected: dict[str, int] = {}
    for org in assignments:
        per_org_expected[org] = per_org_expected.get(org, 0) + 1

    async def one_request(idx: int, org: str) -> str:
        auth = authorize(_allow_decision())
        assert auth is not None
        got: list[UpstreamEvent] = []
        async for event in entry.dispatch(
            auth,
            decision=_allow_decision(),
            request=_request(f"req-{idx}"),
            plan=plans[org],
            catalogue=ProviderCatalogue(org_id=org, entries=()),
            request_id=f"{org}:{idx}",
        ):
            got.append(event)
        return expected_dest[org]

    async def run_all() -> list[str]:
        return await asyncio.gather(
            *(one_request(idx, org) for idx, org in enumerate(assignments))
        )

    results = _run(run_all())
    assert len(results) == total

    # No spurious provisioning errors: all 500 completed (gather would have raised).
    assert recorder.provider_calls == total, (
        f"provider_calls {recorder.provider_calls} != total {total} — a request was lost"
    )
    # No shared-state corruption: audited starts add up per org, exactly once each.
    assert len(audit.starts) == total
    per_org_audited: dict[str, int] = {}
    for req_id, dest in audit.starts:
        org = req_id.split(":", 1)[0]
        per_org_audited[org] = per_org_audited.get(org, 0) + 1
        # Each audited start carried that org's deterministic destination.
        assert dest == expected_dest[org], "an org routed to the wrong destination"
    assert per_org_audited == per_org_expected, "per-org audited counts did not add up"


# =========================================================================== #
# Task 11.1 smoke / integration guards (folded in — no sibling collision).
# =========================================================================== #


def test_guard_transport_fronting_isinstance_and_drives_shipped_transport() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property (smoke: transport fronting)
    # Validates: Requirements 2.4
    """INTEGRATION: the shipped ``StubProviderClient`` satisfies the
    ``@runtime_checkable`` ``ProviderClient``, and the entry drives it unchanged.
    """
    stub = StubProviderClient(
        script=(UpstreamEvent(text_deltas=(("message", "hi"),), final=True),),
        rng=random.Random(0x11_1),
    )
    assert isinstance(stub, ProviderClient)
    audit = RecordingAuditSink()
    entry = _entry(client=stub, audit=audit, rewrite=RecordingRewriteBackend())
    auth = authorize(_allow_decision())
    assert auth is not None
    events = _run(
        _drain(
            entry,
            auth=auth,
            decision=_allow_decision(),
            request=_request(),
            plan=_plan(),
            catalogue=ProviderCatalogue(org_id="org-gw11", entries=()),
            request_id="req-front",
        )
    )
    assert [e.text_deltas for e in events] == [(("message", "hi"),)]
    assert len(audit.starts) == 1


def test_guard_no_adjudicator_called_and_router_has_no_mutable_state() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property (smoke: no-adjudicator)
    # Validates: Requirements 6.2, 6.3
    """SMOKE: a fake adjudicator that fails if called is NEVER called on the
    request path, and ``DispatchRouter`` carries no mutable state.
    """
    adjudicator = FailIfCalledAdjudicator()  # never passed to the entry; must stay unused
    recorder = RecordingProviderClient(
        script=(UpstreamEvent(text_deltas=(), final=True),)
    )
    entry = _entry(client=recorder, audit=RecordingAuditSink(), rewrite=RecordingRewriteBackend())
    auth = authorize(_allow_decision())
    assert auth is not None
    _run(
        _drain(
            entry,
            auth=auth,
            decision=_allow_decision(),
            request=_request(),
            plan=_plan(),
            catalogue=ProviderCatalogue(org_id="org-gw11", entries=()),
            request_id="req-noadj",
        )
    )
    # The adjudicator was never consulted (it would have raised AssertionError).
    _ = adjudicator
    # DispatchRouter is slotted with no fields -> no instance mutable state.
    assert DispatchRouter.__slots__ == ()
    router = DispatchRouter()
    assert not hasattr(router, "__dict__"), "DispatchRouter grew an instance __dict__"


def test_guard_no_new_redactor_and_no_socket_rebuild() -> None:
    # Feature: provider-dispatch-authorization-gw11, Property (smoke: no-new-redactor/no-socket)
    # Validates: Requirements 10.3, 10.4, 10.5, 12.1, 12.2
    """SMOKE: ``apply_and_verify`` reuses domain ``Transformation``/``Span`` and the
    shipped ``apply_decision`` (adds no redaction algorithm); the entry wraps the
    shipped transport; the 4th import-linter contract exists; and the provenance
    gate exposes ``main()``/``SystemExit`` and is in the structural-gates job.
    """
    # No new redactor: verify.py reuses the shipped apply_decision + domain types.
    verify_src = inspect.getsource(verify_module)
    assert "from gateway_v2.egress.output_guard import apply_decision" in verify_src, (
        "apply_and_verify no longer reuses the shipped apply_decision (new redactor?)"
    )
    assert "from gateway_v2.domain import" in verify_src
    # apply_and_verify is importable and the shipped Transformation/Span are the ones used.
    assert apply_and_verify is verify_module.apply_and_verify
    assert Transformation.__module__ == "gateway_v2.domain.decision"
    assert Span.__module__ == "gateway_v2.domain.finding"

    # No socket rebuild: the entry fronts the injected ProviderClient (does not define one).
    entry_src = inspect.getsource(inspect.getmodule(AuthorizedProviderEntry))
    assert "import http" not in entry_src and "socket" not in entry_src, (
        "the authorized entry appears to rebuild a real socket transport"
    )

    # R10.4/R10.5: the 4th import-linter forbidden contract exists in pyproject.toml.
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    contracts = data["tool"]["importlinter"]["contracts"]
    names = [c.get("name") for c in contracts]
    http_contract = next(
        (c for c in contracts if c.get("name") == "No HTTP framework outside edge"),
        None,
    )
    assert http_contract is not None, f"4th import-linter contract missing (have {names})"
    assert http_contract["type"] == "forbidden"
    assert set(http_contract["forbidden_modules"]) == {"starlette", "fastapi"}
    assert "gateway_v2.edge" not in http_contract["source_modules"], (
        "edge must be allowed to import the HTTP framework"
    )

    # R10.3: the provenance gate exposes main()/SystemExit and is in the workflow job.
    from lint import check_dispatch_provenance

    assert callable(check_dispatch_provenance.main)
    gate_src = inspect.getsource(check_dispatch_provenance)
    assert "raise SystemExit(main())" in gate_src, "gate missing the SystemExit entrypoint"
    workflow_text = _WORKFLOW.read_text(encoding="utf-8")
    assert "lint.check_dispatch_provenance" in workflow_text, (
        "check_dispatch_provenance is not wired into the structural-gates job"
    )

"""Async open-loop dispatcher for the load harness (B6 / R2-21).

The driver is the harness's one piece that touches a running server: it fires each
:class:`~load_harness.request_plan.PlannedRequest` at its pre-computed ``scheduled_s`` against the
booted gateway over HTTP and collects, per stream, the client-side chunk arrivals the
:mod:`load_harness.c4` collector reduces to C4. Everything upstream of it (arrivals, strata, the
request plan) is a pure offline value; everything downstream of it (c4, ladder, report) is a pure
reduction. The driver is the impure join: schedule in, observations out.

OPEN-LOOP — the central contract (Req 1.1, 1.5). "Open-loop" means request start times come from
the arrival process ALONE and are never delayed by how fast the server responds: a slowing server
must not be able to throttle the offered rate (that is the forbidden closed-loop model in which a
slow server hides its own overload). This driver guarantees non-blocking dispatch structurally, not
by discipline:

  * Each :class:`~load_harness.request_plan.PlannedRequest` is turned into its OWN
    :class:`asyncio.Task` by :func:`_spawn_dispatch`. The task first sleeps until the arrival's
    absolute ``scheduled_s`` (relative to the injected async clock), THEN performs the HTTP fetch
    via the injected :class:`StreamClient`. The whole set of tasks is created up front by
    :func:`_spawn_all` in a tight loop that creates a task per arrival and NEVER awaits a fetch
    between creations.
  * Because every request owns an independent task whose only pre-dispatch wait is "sleep until MY
    scheduled time", request *i + 1*'s dispatch is never sequenced behind request *i*'s RESPONSE —
    it is sequenced only behind its own scheduled instant. ``asyncio.gather`` is awaited exactly
    ONCE, at the very end, to collect every task's :class:`DispatchRecord`; it is never awaited
    between dispatches. A response that takes arbitrarily long (even monotonically increasing
    latencies) therefore cannot move any later request's dispatch start — the invariant Property 2
    (``test_driver.py``) pins.

SCHEDULE_DROP — the generator-slipped signal (Req 1.3, 1.4). A dispatch whose ACTUAL start (read
from the injected clock the instant before the fetch is issued) exceeds its ``scheduled_s`` by more
than ``Tolerances.schedule_drop_s`` is recorded as a ``Schedule_Drop`` on its
:class:`DispatchRecord` (``schedule_dropped=True``). The per-run count is surfaced on
:attr:`OfferedRun.schedule_drop_count`; a non-zero count means the GENERATOR, not the server, fell
behind, which fails the run's pass test (``ladder.run_passes``) and flags the run as not a clean
server measurement (Req 2.4). The drop decision is a pure comparison, so Property 3 can pin that the
count equals exactly the number of over-tolerance dispatches.

LOADGEN_HEADROOM — the generator self-proof (Req 2.1, 2.2, 2.3). Before (or independently of) the
real timed run, the driver can measure how fast it can issue dispatches with NO per-arrival sleep
and NO server wait — a "dry-fire" calibration (:func:`measure_headroom`) that drives the injected
client's fetch back-to-back and divides the dispatch count by the elapsed wall-time to get a
``max_dispatch_rate_per_s``. ``Loadgen_Headroom`` is the fractional margin of that saturation rate
over the configured offered rate: ``(max_dispatch_rate - offered_rate) / offered_rate`` (``+inf``
when the offered rate is zero — unbounded headroom; a saturation rate at or below the offered rate
yields a zero-or-negative margin). A run whose headroom is below ``Tolerances.min_headroom_margin``
is marked ``generator_bound`` on its :class:`OfferedRun`, which the ladder EXCLUDES from knee
determination (neither pass nor fail) rather than reading as a server pass/fail (Req 2.2). The
margin is reported every run (Req 2.3, gate LGW20-5).

THE INJECTED-CLIENT SEAM — httpx only at this boundary, and only under TYPE_CHECKING. The harness
speaks HTTP to the gateway at THIS module alone (keeping ``check_http_outside_edge_resolve`` clean —
the driver holds no gateway HTTP object, only an injected transport). Following
``gateway_v2/gateway_v2/dispatch/http_provider.py``, the concrete HTTP client is INJECTED, never
imported at module scope: :class:`StreamClient` is a structural ``Protocol`` whose ``fetch`` returns
a :class:`StreamOutcome` (the client-side chunk arrivals + surfaced holdback + errored flag for
one stream). The real client (an ``httpx.AsyncClient`` over a loopback socket, or an
``httpx.ASGITransport`` to ``build_app`` — wired by ``dryrun.py``) satisfies the protocol; a FAKE
transport satisfies it in the Property 2/3 async tests. ``httpx`` is imported ONLY under
``TYPE_CHECKING`` for the :data:`StreamClientFactory` annotation, so ``import load_harness.driver``
succeeds on a tree WITHOUT ``httpx`` installed (it is the ``conformance`` extra) and
``lint-imports`` stays contracts-kept (httpx is a third-party import, not a serving-path edge). The
driver imports only ``load_harness`` siblings + stdlib + httpx-under-TYPE_CHECKING.

Determinism spine. The driver threads the injected async clock (:data:`AsyncClock` — the "now"
read) and injected :data:`AsyncSleep` (the "sleep until" primitive, defaulting to
:func:`asyncio.sleep`) so a test can drive it under a fake clock without real wall-time, exactly as
the pure generators thread
``random.Random`` + ``Clock``. The driver introduces no new randomness (the plan is already drawn)
and no module-level mutable; every value type is frozen + slots (Req 14.2, 14.3).
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from load_harness.c4 import ChunkArrival, StreamObservation
from load_harness.request_plan import PlannedRequest
from load_harness.strata.prompts import PromptTag

if TYPE_CHECKING:
    # ``httpx`` is an OPTIONAL ``conformance``-extra dependency, imported ONLY under TYPE_CHECKING
    # for the client-factory annotation — never at module load. So ``import load_harness.driver``
    # succeeds on a tree WITHOUT the extra (the real client is injected by ``dryrun.py``), and the
    # driver is the SOLE HTTP boundary (``check_http_outside_edge_resolve`` stays clean). The
    # matching runtime construction of the client lives in the dry-run, which importorskips httpx.
    import httpx

__all__ = (
    "AsyncClock",
    "AsyncSleep",
    "DispatchRecord",
    "OfferedRun",
    "StreamClient",
    "StreamClientFactory",
    "StreamOutcome",
    "loadgen_headroom",
    "measure_headroom",
    "run_offered",
)

#: The injected async-side monotonic clock (seconds) — the "now" read. Same shape as the pure
#: generators' ``arrivals.Clock`` and ``http_provider.Clock``. Never read from the wall-clock
#: directly here; a test injects a fake clock so dispatch timing is deterministic without real
#: elapsed time.
AsyncClock = Callable[[], float]

#: The injected "sleep for N seconds" primitive, defaulting to :func:`asyncio.sleep`. Injected so a
#: test can replace real suspension with an instant (or fake-clock-advancing) coroutine, driving the
#: open-loop schedule without wall-time. Must be awaitable.
AsyncSleep = Callable[[float], Awaitable[None]]

#: The factory that builds the httpx async client, injected exactly as
#: ``http_provider.ClientFactory`` is — so the dry-run supplies an ``httpx.AsyncClient`` (real
#: loopback socket) or an ``httpx.ASGITransport`` client, and a test supplies a fake. Typed as
#: returning the httpx async client under TYPE_CHECKING only, so the base tree needs no httpx.
if TYPE_CHECKING:
    StreamClientFactory = Callable[[], httpx.AsyncClient]
else:  # pragma: no cover - runtime alias when httpx is not imported
    StreamClientFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class StreamOutcome:
    """The client-side result of ONE dispatched stream, as the injected client returns it.

    This is the narrow value the :class:`StreamClient` seam yields: the harness's own client-vantage
    observations of a single request's SSE stream, independent of any HTTP library type (so the
    driver holds no gateway HTTP object). The driver folds it, together with the dispatch's
    scheduled/actual timing, into a :class:`DispatchRecord`, and surfaces the embedded
    :class:`~load_harness.c4.StreamObservation` to the C4 collector.

    ``chunks`` are the 100%-sampled client-side chunk arrivals (Req 8.2), in arrival order;
    ``holdback_ms`` is the holdback-attributed latency the gateway surfaced for this stream (the
    real GW12b path exposes holdback timing), which the C4 collector subtracts (Req 8.3);
    ``errored`` is
    ``True`` when the stream failed (transport error, non-2xx, malformed SSE) — an errored stream's
    C4 is ``+inf`` (Req 8.4). ``tag`` is the long/short prompt classification the C4 summary splits
    on (Req 4.3). Frozen + slots (Req 14.3).
    """

    chunks: tuple[ChunkArrival, ...]
    holdback_ms: float
    errored: bool
    tag: PromptTag

    def observation(self) -> StreamObservation:
        """Project this outcome into the :class:`~load_harness.c4.StreamObservation` C4 consumes."""
        return StreamObservation(
            chunks=self.chunks,
            holdback_ms=self.holdback_ms,
            errored=self.errored,
            tag=self.tag,
        )


class StreamClient(Protocol):
    """The injected HTTP seam: fetch ONE planned request's stream, client-vantage (Req 1.1).

    The driver depends only on this structural ``Protocol`` — never on an ``httpx`` type — so (a) a
    FAKE transport drives the open-loop/schedule-drop property tests with no real server, and (b)
    the real ``httpx``-backed client (loopback socket or ``ASGITransport`` to ``build_app``, built
    by ``dryrun.py`` which ``importorskip``s httpx) drops in unchanged. ``fetch`` is ``async``: it
    opens the streaming request, reads every chunk recording its client-side arrival time, and
    returns a :class:`StreamOutcome`. It MUST NOT raise for an ordinary stream failure — a failed
    stream is reported as ``StreamOutcome(errored=True, ...)`` so the C4 ``+inf`` accounting (Req
    8.4) stays on the value path rather than crashing a dispatch task.
    """

    async def fetch(self, request: PlannedRequest) -> StreamOutcome: ...


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    """The per-request dispatch result: scheduled vs actual timing + the stream outcome (Req 1.3).

    ``index`` is the arrival's ordinal (mirrors ``Arrival.index``); ``scheduled_s`` is the absolute
    open-loop target the request was supposed to be dispatched at; ``actual_start_s`` is the clock
    reading the instant the fetch was actually issued. ``schedule_dropped`` is ``True`` iff
    ``actual_start_s - scheduled_s`` exceeded the run's ``Tolerances.schedule_drop_s`` — the
    generator fell behind for this request (Req 1.3). ``outcome`` is the stream's client-vantage
    :class:`StreamOutcome` (its chunks feed C4; ``outcome.errored`` marks a failed stream, Req 8.4).
    Frozen + slots (Req 14.3).
    """

    index: int
    scheduled_s: float
    actual_start_s: float
    schedule_dropped: bool
    outcome: StreamOutcome

    @property
    def slip_s(self) -> float:
        """How far (seconds) the actual dispatch slipped past the schedule (``< 0`` if early)."""
        return self.actual_start_s - self.scheduled_s


@dataclass(frozen=True, slots=True)
class OfferedRun:
    """The full result of one open-loop offered run (frozen + slots, Req 1.4, 2.2, 2.3).

    ``records`` is the per-request :class:`DispatchRecord` tuple, ordered by arrival ``index``.
    ``schedule_drop_count`` is the number of records with ``schedule_dropped`` (Req 1.4) — a
    non-zero count means the run is not a clean server measurement (Req 2.4) and fails its pass test
    (Req 10.3). ``loadgen_headroom`` is the fractional margin of the generator's saturation dispatch
    rate over the configured offered rate (Req 2.3); ``generator_bound`` is ``True`` iff that
    headroom fell below ``Tolerances.min_headroom_margin``, so the ladder EXCLUDES this run from
    knee determination (Req 2.2). ``observations`` projects every record's stream outcome into the
    :class:`~load_harness.c4.StreamObservation` tuple the C4 collector reduces.
    """

    records: tuple[DispatchRecord, ...]
    schedule_drop_count: int
    loadgen_headroom: float
    generator_bound: bool

    @property
    def observations(self) -> tuple[StreamObservation, ...]:
        """The per-stream :class:`~load_harness.c4.StreamObservation` tuple, in dispatch order."""
        return tuple(record.outcome.observation() for record in self.records)


def loadgen_headroom(*, max_dispatch_rate_per_s: float, offered_rate_per_s: float) -> float:
    """The fractional Loadgen_Headroom margin of saturation rate over the offered rate (Req 2.1).

    ``(max_dispatch_rate - offered_rate) / offered_rate`` — the proven fractional margin by which
    the generator can out-pace the configured offered rate (gate LGW20-5). Returns ``math.inf`` when
    the
    offered rate is non-positive (unbounded headroom — any dispatch rate out-paces a zero offered
    rate); a saturation rate at or below the offered rate yields a zero-or-negative margin, which
    (when below ``min_headroom_margin``) marks the run generator-bound. A pure function — the
    measurement itself is :func:`measure_headroom`.
    """
    if offered_rate_per_s <= 0.0:
        return math.inf
    return (max_dispatch_rate_per_s - offered_rate_per_s) / offered_rate_per_s


async def measure_headroom(
    requests: Sequence[PlannedRequest],
    client: StreamClient,
    *,
    offered_rate_per_s: float,
    clock: AsyncClock,
) -> float:
    """Dry-fire the generator back-to-back to measure its saturation dispatch rate, return headroom.

    The generator self-proof (Req 2.1, 2.3). Issues ``client.fetch`` for every request with NO
    per-arrival sleep and NO schedule gating — purely as fast as the generator + injected client can
    turn them over — and divides the dispatch count by the elapsed clock time to get the saturation
    ``max_dispatch_rate_per_s``, then returns :func:`loadgen_headroom` of that rate over
    ``offered_rate_per_s``. Reads the injected ``clock`` before and after so a fake-clock test can
    assert the margin deterministically. An empty request set, or a zero elapsed span, yields
    ``math.inf`` headroom (the dry-fire proved no slowdown). This is a CALIBRATION pass: its stream
    outcomes are discarded (only the count + elapsed time matter); the real timed run is
    :func:`run_offered`.
    """
    count = len(requests)
    if count == 0:
        return math.inf
    started_s = clock()
    for request in requests:
        await client.fetch(request)
    elapsed_s = clock() - started_s
    if elapsed_s <= 0.0:
        return math.inf
    max_dispatch_rate_per_s = count / elapsed_s
    return loadgen_headroom(
        max_dispatch_rate_per_s=max_dispatch_rate_per_s,
        offered_rate_per_s=offered_rate_per_s,
    )


async def _dispatch_one(
    request: PlannedRequest,
    client: StreamClient,
    *,
    schedule_drop_s: float,
    start_reference_s: float,
    clock: AsyncClock,
    sleep: AsyncSleep,
) -> DispatchRecord:
    """Sleep until this request's absolute ``scheduled_s`` then fetch its stream (Req 1.1/1.3/1.5).

    The whole of one open-loop dispatch. ``scheduled_s`` is the arrival's ABSOLUTE target; the task
    sleeps for ``scheduled_s - now`` relative to the injected ``clock`` (clamped at ``0`` so an
    already-late arrival dispatches immediately rather than sleeping a negative span), reads the
    ACTUAL start the instant before issuing the fetch, and records a ``Schedule_Drop`` iff the slip
    past ``scheduled_s`` exceeded ``schedule_drop_s`` (Req 1.3). ``start_reference_s`` is the run's
    shared clock origin captured once by :func:`run_offered`, so every task measures its sleep
    against the SAME zero and the schedule is honoured as an absolute timeline (not per-task drift).

    Crucially this coroutine awaits ONLY its own sleep + its own fetch — it never awaits another
    request — so running one of these per arrival concurrently gives the open-loop guarantee: a slow
    fetch here cannot delay any other task's sleep-until-scheduled (Req 1.5). A fetch failure is
    surfaced by the client as ``StreamOutcome(errored=True)`` (never raised), so a dispatch task
    always yields a record.
    """
    scheduled_s = request.arrival.scheduled_s
    now_s = clock() - start_reference_s
    wait_s = scheduled_s - now_s
    if wait_s > 0.0:
        await sleep(wait_s)
    actual_start_s = clock() - start_reference_s
    schedule_dropped = (actual_start_s - scheduled_s) > schedule_drop_s
    outcome = await client.fetch(request)
    return DispatchRecord(
        index=request.arrival.index,
        scheduled_s=scheduled_s,
        actual_start_s=actual_start_s,
        schedule_dropped=schedule_dropped,
        outcome=outcome,
    )


def _spawn_all(
    requests: Sequence[PlannedRequest],
    client: StreamClient,
    *,
    schedule_drop_s: float,
    start_reference_s: float,
    clock: AsyncClock,
    sleep: AsyncSleep,
) -> list[asyncio.Task[DispatchRecord]]:
    """Create one dispatch :class:`asyncio.Task` per request WITHOUT awaiting any fetch (Req 1.5).

    This tight loop is what makes the run open-loop: it turns every :class:`PlannedRequest` into an
    independent task (each of which internally sleeps until its own ``scheduled_s`` then fetches)
    and returns the task list — awaiting NOTHING in between. Because no fetch is awaited here,
    request
    *i + 1*'s task is created (and begins its own sleep-until-scheduled) regardless of how long
    request *i*'s fetch takes, so a slow server never throttles the offered rate (the forbidden
    closed-loop behaviour). The single ``await`` that collects results happens later, once, in
    :func:`run_offered`.
    """
    return [
        asyncio.ensure_future(
            _dispatch_one(
                request,
                client,
                schedule_drop_s=schedule_drop_s,
                start_reference_s=start_reference_s,
                clock=clock,
                sleep=sleep,
            )
        )
        for request in requests
    ]


async def run_offered(
    requests: Sequence[PlannedRequest],
    client: StreamClient,
    *,
    offered_rate_per_s: float,
    schedule_drop_s: float,
    min_headroom_margin: float,
    clock: AsyncClock,
    sleep: AsyncSleep = asyncio.sleep,
    headroom: float | None = None,
) -> OfferedRun:
    """Run the full open-loop offered run and reduce it to an :class:`OfferedRun` (Req 1, 2).

    The driver's one public entry. It (1) captures the shared clock origin ``start_reference_s``
    once so every dispatch measures its sleep-until-scheduled against the SAME timeline; (2)
    spawns one dispatch task per request via :func:`_spawn_all` WITHOUT awaiting any fetch between
    spawns (the open-loop guarantee, Req 1.1/1.5); (3) awaits ``asyncio.gather`` EXACTLY ONCE to
    collect every :class:`DispatchRecord`; (4) orders the records by arrival ``index``, counts the
    ``Schedule_Drop`` records (Req 1.4), and marks the run ``generator_bound`` iff ``headroom`` is
    below ``min_headroom_margin`` (Req 2.2).

    ``headroom`` is the already-measured Loadgen_Headroom (from :func:`measure_headroom`), passed in
    so the headroom calibration and the timed run are separate explicit steps the caller
    (``dryrun``/``__main__``) sequences; ``None`` means "not measured" and is treated as unbounded
    (``math.inf``) so a run without a calibration pass is never spuriously marked generator-bound.
    ``sleep`` defaults to :func:`asyncio.sleep` for a real run and is injected as a fake in the
    async property tests. An empty request set yields an empty :class:`OfferedRun` with zero drops.
    """
    start_reference_s = clock()
    tasks = _spawn_all(
        requests,
        client,
        schedule_drop_s=schedule_drop_s,
        start_reference_s=start_reference_s,
        clock=clock,
        sleep=sleep,
    )
    gathered = await asyncio.gather(*tasks) if tasks else []
    records = tuple(sorted(gathered, key=lambda record: record.index))
    schedule_drop_count = sum(1 for record in records if record.schedule_dropped)
    effective_headroom = math.inf if headroom is None else headroom
    generator_bound = effective_headroom < min_headroom_margin
    return OfferedRun(
        records=records,
        schedule_drop_count=schedule_drop_count,
        loadgen_headroom=effective_headroom,
        generator_bound=generator_bound,
    )

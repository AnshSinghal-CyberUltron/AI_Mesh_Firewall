"""Client-side C4 collector for the load harness (B6 / R2-21), reconciled with B5's `latency_slo`.

C4 is the firewall-added client-side latency metric: per stream, the **worst (maximum) inter-chunk
client-side latency**, with **100% per-chunk sampling**, **holdback-attributed latency excluded**,
and an **errored stream counted as positive infinity** (Req 8.1–8.4). This module turns a run's
per-stream observations into a :class:`C4Summary` and reconciles the finite subset with the shipped
`gateway_v2/runtime/latency_slo.py` Firewall_Added_Delta definition rather than defining p99 or the
lost-segment-tail exclusion a second time (Req 9).

The one allowed import edge (Req 9, 14.1). This module imports DOWNWARD into
`gateway_v2.runtime.latency_slo` (``PathSamples`` / ``PairedPathSamples`` / ``FirewallDelta`` /
``firewall_added_delta`` / ``DirectBaselineMissing``) and ``gateway_v2.runtime.resources``
(``ResourceContract`` for the pass bound, ``assert_within_slo``), exactly as `state_control` imports
`gateway_v2.domain.state_knobs`. `gateway_v2` never imports `load_harness`, so no serving-path edge
is added and `lint-imports` stays contracts-kept. The harness holds NO SLO/capacity-position literal
of its own: the bound is read from ``ResourceContract.target_p99_ms`` and the ≈ 40 ms lost-segment
tail comes from `latency_slo`, never re-declared here (Req 9.3).

TWO DISTINCT p99 COMPUTATIONS — both are needed and they are deliberately different:

  (1) The headline **C4 p99 over ALL offered streams including the ``+∞`` ones** (Req 8.5),
      :attr:`C4Summary.c4_p99_ms`. This is the run's REPORTED C4 and the honest pass metric: it is
      the nearest-rank p99 over the full sorted list of every offered stream's C4 — finite values
      AND the ``math.inf`` entries for errored streams. Enough errored streams push the p99 rank
      onto an ``inf`` entry, so ``c4_p99_ms`` becomes ``+∞`` and the run fails. This escalation is a
      HARNESS-LEVEL overlay; `latency_slo` only ever reasons over finite client samples, so it
      cannot and must not compute this number — the harness owns it. Computed by
      :func:`c4_p99_over_all_streams` with the SAME nearest-rank definition `latency_slo._p99_ms`
      uses, re-expressed here only because `latency_slo` refuses an ``inf`` (it rejects non-finite
      sample sets by raising on p99 of a set it was never meant to hold).

  (2) The **finite Firewall_Added_Delta** against a Direct_Path baseline (Req 9.1/9.2),
      :func:`reconcile_firewall_delta`. The finite per-stream C4 values become a
      `latency_slo.PathSamples` (the through-firewall path) and the delta vs the Direct_Path
      baseline is computed by ``latency_slo.firewall_added_delta(...)`` — the SAME
      ``exclude_lost_segment_tails`` + nearest-rank p99, NO duplicate computation. The gate's SLO
      check uses THIS finite delta via ``latency_slo.assert_within_slo(delta, contract)``.

They answer different questions: (1) is "what C4 did the run exhibit, counting failures as
infinitely bad?"; (2) is "how much latency did the firewall add over a baseline network, over the
finite well-behaved samples?". The reported run C4 is (1); the SLO assertion is (2).

Error Handling. ``DirectBaselineMissing`` raised by `latency_slo` (absent baseline, or every direct
sample trimmed as a lost-segment tail) PROPAGATES out of :func:`reconcile_firewall_delta` — the
harness surfaces it as a measurement error rather than fabricating a delta, consistent with the
shipped `latency_slo` fail-closed contract.

Self-contained (determinism spine). The collector is a pure function of its per-stream observations;
it takes no clock, no network, no module-global ``random``. Holdback is modelled as a per-stream
holdback-ms figure the gateway surfaces (the real GW12b path exposes holdback timing), excluded from
the worst-chunk computation (Req 8.3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from gateway_v2.runtime import latency_slo
from gateway_v2.runtime.latency_slo import (
    DirectBaselineMissing,
    FirewallDelta,
    PairedPathSamples,
    PathSamples,
)
from gateway_v2.runtime.resources import ResourceContract
from load_harness.strata.prompts import PromptTag

__all__ = (
    "C4Summary",
    "ChunkArrival",
    "DirectBaselineMissing",
    "StreamC4",
    "StreamObservation",
    "assert_within_slo",
    "c4_p99_over_all_streams",
    "reconcile_firewall_delta",
    "summarize",
)


@dataclass(frozen=True, slots=True)
class ChunkArrival:
    """One client-side chunk-arrival observation for a stream (frozen + slots, Req 8.1/8.2).

    ``index`` is the chunk's 0-based position in the stream; ``arrival_s`` is the client-side
    wall-clock time (seconds) at which the chunk was observed at the client vantage (C39). The
    inter-chunk latency into this chunk is ``arrival_s`` minus the previous chunk's ``arrival_s``;
    the first chunk of a stream has no inter-chunk predecessor and contributes no latency. 100% of
    chunks are sampled (Req 8.2): every observed chunk is one of these.
    """

    index: int
    arrival_s: float


@dataclass(frozen=True, slots=True)
class StreamObservation:
    """The raw per-stream client observations the collector reduces to a :class:`StreamC4`.

    ``chunks`` are the 100%-sampled client-side chunk arrivals (Req 8.2), expected in arrival
    order; ``holdback_ms`` is the holdback-attributed latency the gateway surfaced for this stream
    (the real GW12b path exposes holdback timing), SUBTRACTED from the stream's worst inter-chunk
    latency so the firewall's bounded-holdback hold is not charged as C4 (Req 8.3). ``errored``
    marks a stream that failed; an errored stream's C4 is ``+∞`` regardless of its chunks (Req 8.4).
    ``tag`` is the long/short prompt classification from the prompt stratum (``strata/prompts.py``),
    on which the C4 summary splits long-prompt vs ≤ 1,024-token statistics (Req 4.3 tie-in).
    """

    chunks: tuple[ChunkArrival, ...]
    holdback_ms: float
    errored: bool
    tag: PromptTag


@dataclass(frozen=True, slots=True)
class StreamC4:
    """One stream's reduced C4: worst holdback-excluded inter-chunk latency, or ``+∞`` on error.

    ``worst_chunk_ms`` is the maximum per-chunk client-side inter-chunk latency with the stream's
    holdback-attributed latency subtracted (Req 8.1/8.3); for an errored stream it is ``math.inf``
    (Req 8.4). ``errored`` mirrors the source observation so a consumer can tell an infinite C4 due
    to error apart from a (never produced) infinite finite measurement.
    """

    worst_chunk_ms: float
    errored: bool

    @property
    def is_finite(self) -> bool:
        """True iff this stream contributed a finite C4 (i.e. did not error)."""
        return math.isfinite(self.worst_chunk_ms)


@dataclass(frozen=True, slots=True)
class C4Summary:
    """A run's C4 summary (frozen + slots, Req 8.5 + Req 4.3 split).

    ``c4_p99_ms`` is the headline C4 p99 over ALL offered streams INCLUDING the ``+∞`` errored ones
    (computation (1) in the module docstring, Req 8.5) — the run's reported C4 and the honest pass
    metric. ``long_prompt`` / ``short_prompt`` are the FINITE per-stream C4 samples split by the
    prompt stratum's long/short tag (Req 4.3), each a `latency_slo.PathSamples` ready to feed
    ``latency_slo.firewall_added_delta`` against a Direct_Path baseline (computation (2)); errored
    streams contribute ``+∞`` to ``c4_p99_ms`` but NOT to these finite sample sets.
    """

    c4_p99_ms: float
    long_prompt: PathSamples
    short_prompt: PathSamples


_MS_PER_S = 1000.0
"""Milliseconds per second — a unit conversion, not a capacity position.

Chunk arrivals are observed in seconds (client wall-clock); C4 latencies are reported in
milliseconds to match `latency_slo`'s ``samples_ms`` contract. A dimensional constant, the same
category as `latency_slo`'s own ms/second handling; not a CPU/RAM/FD/RPS capacity literal.
"""


def reduce_stream(observation: StreamObservation) -> StreamC4:
    """Reduce one stream's raw observations to its :class:`StreamC4` (Req 8.1/8.3/8.4).

    An errored stream's C4 is ``math.inf`` (Req 8.4), independent of its chunks. Otherwise the
    stream's C4 is the MAXIMUM inter-chunk client-side latency across its 100%-sampled chunks
    (Req 8.1/8.2) with the surfaced holdback-attributed latency subtracted (Req 8.3), floored at
    ``0.0`` so a holdback figure larger than the worst raw gap never yields a negative C4. A stream
    with fewer than two chunks has no inter-chunk gap and contributes a C4 of ``0.0`` (no observed
    inter-chunk latency), which is the correct "nothing bad happened" finite value.
    """
    if observation.errored:
        return StreamC4(worst_chunk_ms=math.inf, errored=True)
    chunks = observation.chunks
    worst_gap_ms = 0.0
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        gap_ms = (cur.arrival_s - prev.arrival_s) * _MS_PER_S
        if gap_ms > worst_gap_ms:
            worst_gap_ms = gap_ms
    holdback_excluded = worst_gap_ms - observation.holdback_ms
    return StreamC4(worst_chunk_ms=max(holdback_excluded, 0.0), errored=False)


def c4_p99_over_all_streams(stream_c4s: tuple[StreamC4, ...]) -> float:
    """Nearest-rank C4 p99 over ALL offered streams, INCLUDING the ``+∞`` errored ones (Req 8.5).

    Computation (1) in the module docstring — the run's REPORTED C4. The nearest-rank definition is
    identical to `latency_slo._p99_ms` (value at rank ``ceil(0.99 * n)`` of the sorted samples,
    1-indexed), re-expressed here only because this list intentionally contains ``math.inf`` for
    errored streams and `latency_slo` is a finite-sample authority that must not be handed a
    non-finite set. When the share of errored streams pushes the p99 rank onto an ``inf`` entry the
    result is ``math.inf``, so enough errors fail the SLO (Req 8.5). Raises ``ValueError`` on an
    empty run — a run with no offered streams has no C4 to report.
    """
    n = len(stream_c4s)
    if n == 0:
        raise ValueError("C4 p99 is undefined for a run with no offered streams")
    ordered = sorted(c4.worst_chunk_ms for c4 in stream_c4s)
    rank = -(-99 * n // 100)  # ceil(0.99 * n) via integer arithmetic (matches latency_slo._p99_ms)
    index = min(rank, n) - 1
    return ordered[index]


def summarize(observations: tuple[StreamObservation, ...]) -> C4Summary:
    """Reduce a run's per-stream observations to a :class:`C4Summary` (Req 8.5 + Req 4.3 split).

    Reduces each stream to a :class:`StreamC4` (:func:`reduce_stream`), takes the headline C4 p99
    over ALL streams including the errored ``+∞`` ones (:func:`c4_p99_over_all_streams`, Req 8.5),
    and partitions the FINITE per-stream C4 values by the prompt stratum's long/short tag (Req 4.3)
    into the two `latency_slo.PathSamples` the Firewall_Added_Delta reconciliation consumes. Errored
    streams raise the headline p99 but contribute no finite sample to either tagged path. Raises
    ``ValueError`` (via :func:`c4_p99_over_all_streams`) on an empty run.
    """
    stream_c4s = tuple(reduce_stream(obs) for obs in observations)
    c4_p99_ms = c4_p99_over_all_streams(stream_c4s)
    long_samples: list[float] = []
    short_samples: list[float] = []
    for obs, stream_c4 in zip(observations, stream_c4s, strict=True):
        if not stream_c4.is_finite:
            continue
        if obs.tag is PromptTag.LONG:
            long_samples.append(stream_c4.worst_chunk_ms)
        else:
            short_samples.append(stream_c4.worst_chunk_ms)
    return C4Summary(
        c4_p99_ms=c4_p99_ms,
        long_prompt=PathSamples(tuple(long_samples)),
        short_prompt=PathSamples(tuple(short_samples)),
    )


def reconcile_firewall_delta(
    through_firewall: PathSamples,
    direct: PathSamples,
) -> FirewallDelta:
    """Compute the finite Firewall_Added_Delta by DELEGATING to `latency_slo` (Req 9.1/9.2).

    Computation (2) in the module docstring. The finite per-stream C4 samples (e.g.
    ``C4Summary.long_prompt`` / ``short_prompt``, or their union) are the ``through_firewall`` path;
    ``direct`` is the Direct_Path baseline measured over the SAME network. The delta is computed by
    ``latency_slo.firewall_added_delta`` — the SAME ``exclude_lost_segment_tails`` and nearest-rank
    p99 as the shipped B5 definition, with NO duplicate computation here (Req 9.1/9.2). This is the
    delta the gate asserts against the SLO bound (:func:`assert_within_slo`).

    ``DirectBaselineMissing`` (absent baseline, or every direct sample trimmed as a lost-segment
    tail) PROPAGATES out unchanged (Error Handling) — the harness never fabricates a delta.
    """
    return latency_slo.firewall_added_delta(
        PairedPathSamples(through_firewall=through_firewall, direct=direct),
    )


def assert_within_slo(delta: FirewallDelta, contract: ResourceContract) -> None:
    """Assert the finite Firewall_Added_Delta is within the SLO bound via `latency_slo`.

    The pass bound is read from ``ResourceContract.target_p99_ms`` via
    ``latency_slo.assert_within_slo`` (Req 9.3); the harness holds NO SLO/capacity literal of its
    own. Raises ``ValueError`` iff the finite delta p99 exceeds ``contract.target_p99_ms``. This
    gates on computation (2) — the finite firewall-added delta — NOT the
    all-streams-including-``+∞`` headline C4, which is the run's reported metric (computation (1)).
    """
    latency_slo.assert_within_slo(delta, contract)

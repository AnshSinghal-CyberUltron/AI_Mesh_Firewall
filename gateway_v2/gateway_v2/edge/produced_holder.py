"""Per-request sink for the stream pipeline's stats, plus the one helper that records a cut.

Split out of `edge/routes.py` (799 of 800 lines) in GW35. Both `StreamPipeline.run` and
`StrictWithholdPipeline.run` return stats carrying `produced_mode`. `ChatRoute._attempt` stashes
them here so `_finish_stream` can emit the `amf.trace` frame just before `[DONE]`. A per-request
instance, never module-level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gateway_v2.domain.failure_class import is_no_action, is_operational_failure

if TYPE_CHECKING:
    from gateway_v2.edge.request_holds import RequestHolds
    from gateway_v2.egress.stream import StreamStats
    from gateway_v2.egress.strict import StrictStats

__all__ = ("ProducedModeHolder", "record_cut")


class ProducedModeHolder:
    """A per-request mutable sink for the pipeline's returned stats (GW13 C5).

    ``stats`` is ``None`` until an attempt completed. ``cut_reason`` is the kill-latch reason that
    ended the stream, when it is not a kill-switch cut.
    """

    __slots__ = ("cut_reason", "holds", "stats")

    def __init__(self, holds: RequestHolds | None = None) -> None:
        self.holds = holds
        self.stats: StreamStats | StrictStats | None = None
        self.cut_reason: str | None = None

    def has_trace(self) -> bool:
        """True when a trace frame can be built: stats exist, or a failure was recorded."""
        return self.stats is not None or (self.holds is not None and bool(self.holds.failure_code))


def record_cut(produced: ProducedModeHolder, reason: str) -> None:
    """Record a kill-latch cut on the ledger. A kill-switch cut writes nothing (no action).

    A fault (timeout, malformed upstream) is a ``failure_code`` only. A policy cut is also a
    ``block`` output action, which overrides the synthetic allow the pass-through loop recorded.
    ``error`` is never forwarded to the output action.
    """
    if is_no_action(reason):
        return
    produced.cut_reason = reason
    holds = produced.holds
    if holds is None:
        return
    holds.note_failure(reason)
    if not is_operational_failure(reason):
        holds.note_output_action("block", reason)
    stats = produced.stats
    if stats is not None and getattr(stats, "output_action", None) in (None, "allow"):
        stats.output_action = "error" if is_operational_failure(reason) else "block"

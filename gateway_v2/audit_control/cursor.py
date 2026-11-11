"""Stream ids, cursors, and the subtraction that makes `records_lost` exact.

A Redis stream id is `<millis>-<seq>`: two integers. Three things depend on comparing them
correctly, and all three break the same way if the comparison is done on the text:

* the exporter's page read (`XRANGE` above an exclusive cursor);
* whether a record is above or below the durable mark;
* whether the oldest surviving record is NEWER than the durable mark, which is the loss test.

`'10-1' < '9-1'` as text. A string comparison would therefore make loss detection skip a whole
decade of ids, silently, and the resulting `records_lost` would read zero in exactly the case the
card exists to catch. So ids are parsed once, at the edge, into a totally-ordered pair.

**The loss rule, and why it is counts rather than positions.** `records_lost` is:

    lost = acknowledged - durable - pending

where `acknowledged` is the lifetime number of records the WRITER appended for that tenant (it
counted them), `durable` is what the sink holds, and `pending` is what is still in the stream
above the durable cursor. This is exactly the card's *"acknowledged-vs-durable high-water mark"*,
and it is exact in every case:

* a flush of a stream with 100 appended and 64 exported gives `100 - 64 - 0 = 36`;
* a trim that happens entirely BELOW the cursor gives `100 - 100 - 0 = 0`, correctly reporting
  retention rather than loss;
* a trim that overtakes the cursor gives the number of records it took that nobody had.

The first version of this derived loss from the writer's `XTRIM` count instead, and it was
wrong in the most important case: a FLUSH removes the already-durable records too, so the trim
count over-states the loss — it would have reported 100 lost where 36 were lost. Positions
cannot fix that, because the gap's SIZE is not recoverable from ids (they are not dense). Counts
can, because both ends of the subtraction are counted by the component that knows.

Positions are still needed for one thing: deciding where the cursor must JUMP to after a trim,
so it does not keep asking for records that no longer exist.
"""

from __future__ import annotations

from dataclasses import dataclass

ZERO_ID = "0-0"
"""Below every real id. A fresh cursor starts here, so the first page is the whole stream."""


@dataclass(frozen=True, slots=True, order=True)
class StreamId:
    """A parsed stream id. `order=True` gives the correct total order for free."""

    ms: int
    seq: int

    def __str__(self) -> str:
        return f"{self.ms}-{self.seq}"

    @property
    def is_zero(self) -> bool:
        return self.ms == 0 and self.seq == 0


ZERO = StreamId(0, 0)


def parse_id(raw: str) -> StreamId:
    """`<millis>-<seq>` into a comparable pair. Raises on anything else.

    Deliberately strict. A malformed id that silently became `(0, 0)` would read as "below
    everything", so a cursor would never advance past it and the export would loop on one page
    forever — the GW05c/R2-02 `store_ahead` defect, in a different mechanism.
    """
    text = raw.strip()
    if not text:
        raise ValueError("an empty stream id has no position")
    head, _, tail = text.partition("-")
    try:
        return StreamId(int(head), int(tail or 0))
    except ValueError as exc:
        raise ValueError(f"{raw!r} is not a <millis>-<seq> stream id") from exc


@dataclass(frozen=True, slots=True)
class OrgCursor:
    """One tenant's export position, as it is stored.

    `durable` and `durable_records` move together, inside the same transaction as the page they
    describe. `acknowledged` is the store's head as last observed, which is a reading rather than
    a commitment — the gap between the two IS the window the card asks to be visible.
    """

    org_id: str
    durable: StreamId = ZERO
    durable_records: int = 0
    acknowledged: StreamId = ZERO
    acknowledged_records: int = 0
    records_lost: int = 0

    @property
    def behind(self) -> int:
        """Records the store has acknowledged that nothing durable holds yet.

        Normal and non-zero during healthy operation: it is the export queue depth. It is only
        alarming when it stops falling, which is what the alert rule watches rather than this.
        """
        return max(self.acknowledged_records - self.durable_records - self.records_lost, 0)


def total_loss(acknowledged: int, durable: int, pending: int) -> int:
    """`acknowledged - durable - pending`, floored at zero. The card's subtraction, exactly.

    This is a LIFETIME total, not a delta, so it is idempotent: calling it twice with the same
    inputs gives the same answer, and a round that double-counted its own loss is impossible by
    construction. The exporter stores it rather than accumulating into it for that reason.

    Floored at zero because the three inputs are observed at slightly different moments — a
    record appended between reading `durable` and reading `pending` would otherwise show as
    negative loss, and a counter that can go backwards publishes negative rates (R2-11, M4).
    """
    return max(acknowledged - durable - pending, 0)

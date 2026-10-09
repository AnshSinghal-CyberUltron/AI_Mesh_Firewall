"""The audit writer's Valkey/Redis adapter. Append, then trim to the budget, in one round trip.

    append()   XADD  {rv2}:audit:<org>  * r <payload>     ... once per record
               XTRIM {rv2}:audit:<org>  MAXLEN ~ <n>      ... once per TOUCHED org

Two decisions carry the whole card, and both look like details:

**`XTRIM`, not `XADD ... MAXLEN`.** Capping on the `XADD` is one fewer command and is what RC2
did. It is also unusable here, because `XADD` does not report what it displaced, so the only
honest thing a writer could publish is "we think the cap held". `XTRIM` returns the number of
entries it removed, so `audit_trimmed_records` is an exact count of records that left the store.
R2-11 (M3) measured the alternative: a flush erased 36% of the audit records while
`audit_completeness_ratio` read 1.0. A number that cannot be wrong is the point.

**`approximate=True`.** Exact trimming walks to an entry boundary; approximate trimming removes
whole radix-tree nodes, which is why it is cheap enough to run on every batch. It removes at most
about 10,000 entries per call by default, so a stream far above its cap at upgrade converges over
successive batches rather than stalling one batch for a long time — stated here because it is the
reason a freshly-bounded deployment does not drop to its budget on the first write.

The command list is fixed and short on purpose, the same discipline `store_valkey.py` documents:
no `XLEN` per record, no `MEMORY USAGE` per entry, nothing proportional to the stream. The per-org
byte model in `audit/` is what replaces measuring, and it is checked against real measurements in
the tests rather than trusted.

Operation timeouts are the caller's business, as with `ValkeyStateStore`: the process start-up
path runs `require_bounded_client`. An audit write is not on the request path, so its ceiling is
not the state refresh period — but it is still bounded, because a writer blocked forever on a
partitioned store silently becomes a writer that drops everything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from gateway_v2.runtime.store_keys import KEYS, StoreKeys

FIELD = "r"
"""The single stream field holding the serialized record. One short name, because it is paid
once per entry and a 2,000,000-entry stream pays it 2,000,000 times."""


@dataclass(frozen=True, slots=True)
class AuditBatchResult:
    """What one batch did. Every number is counted, never inferred.

    `written` is what the store ACKNOWLEDGED, which is deliberately not the same thing as what is
    durable — the acknowledged-vs-durable distinction is the card's, and conflating them here is
    how `audit_completeness_ratio` came to read 1.0 while a third of the records were gone.
    """

    written: int
    trimmed: int
    written_by_org: Mapping[str, int] = field(default_factory=dict)
    """Records appended per tenant. The exporter's `acknowledged` half of the subtraction comes
    from here: once a record is trimmed, nothing in the store remembers it existed, so the
    count has to be taken at the moment it was written."""

    trimmed_by_org: Mapping[str, int] = field(default_factory=dict)
    ids: tuple[str, ...] = ()
    """The stream ids the store assigned, in batch order. The exporter's high-water mark needs
    them, and they are free here — `XADD` already returns each one."""


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


class ValkeyAuditStore:
    """Append-and-trim over `redis.asyncio`. The client is injected; pooling is GW03's."""

    def __init__(self, client: Any, keys: StoreKeys = KEYS) -> None:
        self._client = client
        self._keys = keys

    @property
    def keys(self) -> StoreKeys:
        return self._keys

    async def append(
        self,
        records: Sequence[tuple[str, bytes]],
        maxlens: Mapping[str, int],
    ) -> AuditBatchResult:
        """One pipelined round trip: every record appended, every touched stream trimmed.

        `records` is `(org, payload)` in emit order. `maxlens` is the derived cap per org; an org
        absent from it is appended and NOT trimmed, which is the honest behaviour when the budget
        is unknown — the alternative is inventing a cap, and the per-org upper bound already
        covers that case at the caller.

        Not a transaction. There is nothing to make atomic: an append that lands and a trim that
        does not simply leaves the stream long for one batch, and the next batch trims it. A
        `MULTI` here would buy an invariant nobody reads and cost a round trip under contention.
        """
        if not records:
            return AuditBatchResult(written=0, trimmed=0)

        touched: dict[str, int] = {}
        async with self._client.pipeline(transaction=False) as pipe:
            for org, payload in records:
                pipe.xadd(self._keys.audit_stream(org), {FIELD: payload})
                touched[org] = touched.get(org, 0) + 1
            trims = [org for org in touched if org in maxlens]
            for org in trims:
                pipe.xtrim(self._keys.audit_stream(org), maxlen=maxlens[org], approximate=True)
            answers = await pipe.execute()

        ids = tuple(_as_text(answer) for answer in answers[: len(records)])
        removed = answers[len(records):]
        by_org = {
            org: _as_int(count) for org, count in zip(trims, removed, strict=False) if count
        }
        return AuditBatchResult(
            written=len(records),
            trimmed=sum(by_org.values()),
            written_by_org=dict(touched),
            trimmed_by_org=by_org,
            ids=ids,
        )

    async def length(self, org: str) -> int:
        """`XLEN` for one org. For operators, the exporter and the tests — never per record."""
        return _as_int(await self._client.xlen(self._keys.audit_stream(org)))

    async def read(
        self,
        org: str,
        *,
        after: str = "0-0",
        limit: int = 256,
    ) -> tuple[tuple[str, bytes], ...]:
        """One `XRANGE` page above an exclusive id. The exporter's read, bounded by `limit`.

        Exclusive because the caller's cursor is a record it has already made durable; re-reading
        it would double-count it against the durable high-water mark.
        """
        entries = await self._client.xrange(
            self._keys.audit_stream(org), min=f"({after}", max="+", count=limit,
        )
        out: list[tuple[str, bytes]] = []
        for entry_id, fields in entries:
            payload = _payload_of(fields)
            if payload is not None:
                out.append((_as_text(entry_id), payload))
        return tuple(out)

    async def first_id(self, org: str) -> str | None:
        """The oldest id still in the stream, or None when it is empty.

        This is what makes loss EXACT rather than estimated: a durable cursor below the oldest
        surviving id means the records between them were trimmed before they were exported, and
        that gap is `records_lost`.
        """
        entries = await self._client.xrange(
            self._keys.audit_stream(org), min="-", max="+", count=1,
        )
        if not entries:
            return None
        return _as_text(entries[0][0])


def _payload_of(fields: object) -> bytes | None:
    """Pull the one field back out, tolerating a client that decodes responses."""
    if not isinstance(fields, Mapping):
        return None
    value = fields.get(FIELD)
    if value is None:
        value = fields.get(FIELD.encode("ascii"))
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return None

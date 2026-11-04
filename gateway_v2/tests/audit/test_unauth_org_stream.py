"""GW35: the reserved ``_unauthenticated`` org is an ordinary audit stream."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from gateway_v2.audit.budget import StreamBudget
from gateway_v2.audit.record import UNAUTHENTICATED_ORG, AuditRecord, admission
from gateway_v2.audit.sink import AuditSink
from gateway_v2.domain.audit_knobs import AuditMemoryKnobs
from gateway_v2.runtime.store_audit import AuditBatchResult


class _Store:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bytes]] = []

    async def append(
        self, records: Sequence[tuple[str, bytes]], maxlens: Mapping[str, int],
    ) -> AuditBatchResult:
        self.rows.extend(records)
        assert set(maxlens) == {UNAUTHENTICATED_ORG}
        return AuditBatchResult(written=len(records), trimmed=0)


def test_the_reserved_org_is_written_with_a_per_org_byte_share() -> None:
    store = _Store()
    sink = AuditSink(store, StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 2), queue_depth=4)
    record: AuditRecord = admission(
        request_id="zs-1", org_id=UNAUTHENTICATED_ORG, outcome="rejected",
        reason="invalid_api_key", recorded_at_ns=1, status=401,
    )
    assert sink.emit(record) is True

    async def go() -> None:
        task = asyncio.create_task(sink.run())
        for _ in range(100):
            if store.rows:
                break
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(go())
    assert [org for org, _ in store.rows] == [UNAUTHENTICATED_ORG]
    assert b"invalid_api_key" in store.rows[0][1]

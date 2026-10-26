"""The dev profile enqueues through AuditSink and the writer can be stopped."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from gateway_v2.audit.budget import StreamBudget
from gateway_v2.audit.record import AuditPhase, AuditRecord
from gateway_v2.audit.sink import AuditSink
from gateway_v2.domain.audit_knobs import AuditMemoryKnobs
from gateway_v2.edge.dev_audit import DevAudit, build_dev_audit
from gateway_v2.runtime.store_audit import AuditBatchResult


class _Store:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bytes]] = []

    async def append(
        self,
        records: Sequence[tuple[str, bytes]],
        maxlens: Any,
    ) -> AuditBatchResult:
        del maxlens
        self.rows.extend(records)
        return AuditBatchResult(written=len(records), trimmed=0)


def _record() -> AuditRecord:
    return AuditRecord(
        request_id="zs-dev-org-1",
        org_id="dev-org",
        phase=AuditPhase.INPUT,
        outcome="allow",
        recorded_at_ns=1,
    )


def test_build_uses_the_injected_client_and_emits_without_connecting() -> None:
    client = object()
    audit = build_dev_audit(
        redis_url="redis://unused",
        timeout_s=0.1,
        queue_depth=4,
        env={},
        client=client,
    )
    assert audit.emit(_record()) is True


def test_writer_stores_one_record_and_shutdown_stops_it() -> None:
    store = _Store()
    sink = AuditSink(
        store,
        StreamBudget(AuditMemoryKnobs(budget_mb=1), lambda: 1),
        queue_depth=4,
    )
    audit = DevAudit(sink, object(), owns_client=False)

    async def _run() -> None:
        await audit.startup()
        assert audit.emit(_record()) is True
        for _ in range(50):
            if store.rows:
                break
            await asyncio.sleep(0.01)
        assert len(store.rows) == 1
        assert b"zs-dev-org-1" in store.rows[0][1]
        await audit.shutdown()

    asyncio.run(_run())

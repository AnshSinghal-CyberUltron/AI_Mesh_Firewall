"""Admission-side backpressure tests (R2-07 / GW19), task 12.2.

Covers the credit-based bounded stream-slot buffering built on
:class:`gateway_v2.admit.queues.BackpressureBuffer` and exposed via
:meth:`gateway_v2.admit.admission.AdmissionController.stream_credit`:

* a slow consumer's buffered bytes stay within its declared byte credit (Req 10.1);
* total streaming memory stays bounded across any number of slow consumers (Req 10.2);
* a fast consumer draws only on its own credit and is unaffected by slow consumers (Req 10.3).

The byte credit is the egress slot bound carried on ``AdmissionBounds`` (ultimately
``ResourceContract.stream_buffer_bytes``). Admission owns only this admission-side bound and does
not import ``egress`` (layer rule); the real SSE transport is ``egress/backpressure.py`` /
``egress/stream.py`` (GW13). Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.queues import BackpressureBuffer
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor

_CREDIT_BYTES = 1024


def _controller(credit_bytes: int = _CREDIT_BYTES) -> AdmissionController:
    bounds = AdmissionBounds(
        concurrency=1_000,
        request_depth=1_000,
        guard_depth=1_000,
        dispatch_depth=1_000,
        egress_depth=credit_bytes,  # the egress byte bound doubles as the stream credit
        audit_depth=1_000,
    )
    return AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=1.0,
        clock=lambda: 0.0,
    )


def test_slow_consumer_buffered_bytes_stay_within_the_credit() -> None:
    """A slow consumer that never reads is capped at its byte credit (Req 10.1)."""
    buffer = BackpressureBuffer(_CREDIT_BYTES)
    fed = 0
    # Produce far more than the credit; the consumer never drains (slow).
    for _ in range(1_000):
        receipt = buffer.offer(64)
        if receipt.accepted:
            fed += 64
        assert buffer.buffered_bytes <= _CREDIT_BYTES
    # Backpressure engaged well before 1000*64 bytes were buffered.
    assert buffer.buffered_bytes <= _CREDIT_BYTES
    assert fed <= _CREDIT_BYTES


def test_total_memory_bounded_across_many_slow_consumers() -> None:
    """N slow consumers each cap at their own credit → total <= N * credit (Req 10.2)."""
    seed = 0x19_B9
    rng = random.Random(seed)
    ctl = _controller()
    n_consumers = rng.randint(2, 50)
    buffers = [ctl.stream_credit(f"c{i}") for i in range(n_consumers)]
    # Hammer every consumer with production; none of them read.
    for buf in buffers:
        for _ in range(500):
            buf.offer(rng.randint(1, 128))
    total = sum(buf.buffered_bytes for buf in buffers)
    assert total <= n_consumers * _CREDIT_BYTES, (
        f"seed={seed:#x} total streaming memory {total} exceeded the aggregate credit"
    )
    for buf in buffers:
        assert buf.buffered_bytes <= _CREDIT_BYTES, f"seed={seed:#x} a consumer exceeded its credit"


def test_fast_consumer_unaffected_by_slow_consumers() -> None:
    """A fast consumer (reads as it is fed) keeps accepting while a slow one is backpressured."""
    ctl = _controller()
    slow = ctl.stream_credit("slow")
    fast = ctl.stream_credit("fast")
    # Fill the slow consumer so it is fully backpressured.
    while slow.offer(128).accepted:
        pass
    assert slow.available == 0 or slow.buffered_bytes <= _CREDIT_BYTES
    assert slow.offer(128).accepted is False  # slow consumer is blocked
    # The fast consumer reads as fast as it is fed; it must keep accepting unaffected.
    for _ in range(10_000):
        receipt = fast.offer(128)
        assert receipt.accepted is True, "fast consumer was blocked by a slow consumer's backlog"
        fast.drain(128)  # fast consumer reads immediately
    assert fast.buffered_bytes == 0


def test_per_consumer_credit_is_independent() -> None:
    """Each consumer has its own credit; draining one does not free another's (Req 10.3)."""
    ctl = _controller()
    a = ctl.stream_credit("a")
    b = ctl.stream_credit("b")
    while a.offer(256).accepted:
        pass
    assert a.offer(256).accepted is False
    # b is untouched by a's saturation.
    assert b.buffered_bytes == 0
    assert b.offer(256).accepted is True
    # Draining a frees only a's credit.
    a.drain(256)
    assert a.offer(256).accepted is True
    assert b.buffered_bytes == 256


def test_stream_credit_returns_the_same_buffer_per_consumer() -> None:
    """``stream_credit`` is idempotent per consumer id (lazily created, then reused)."""
    ctl = _controller()
    first = ctl.stream_credit("c")
    second = ctl.stream_credit("c")
    assert first is second
    assert first.credit_bytes == _CREDIT_BYTES

"""F1a-3 plan revision 3, §3.7 (answers A-S3): the keep-alive mechanism.

Reviewer A showed (review 5871181186, test_keepalive_tick_by_wait_for_slices_kills_the_routed_stream)
that ticking keep-alives with `wait_for(it.__anext__(), t)` cancels the upstream read and closes the
attempt, while a non-cancelling wait on ONE task that owns the pending `__anext__` keeps the event.
This probe adds the teardown order: closing the iterator while that task is still pending raises
"asynchronous generator is already running" (the source is never closed), and cancelling and awaiting the
task first closes it cleanly. Runs against develop's `dispatch.stall_guard.bounded_events`.

  PYTHONPATH=<worktree>/gateway_v2 python keepalive_owner_task.py
"""
from __future__ import annotations

import asyncio
import json

from gateway_v2.dispatch.provider import UpstreamEvent
from gateway_v2.dispatch.stall_guard import StallBounds, bounded_events

BOUNDS = StallBounds(first_event_s=30.0, gap_s=20.0, total_s=299.94, teardown_s=0.06)


def source(log: list[str]):
    async def gen():
        try:
            await asyncio.sleep(0.25)
            yield UpstreamEvent(text_deltas=(("content", "late"),), final=False)
            await asyncio.sleep(30)
            yield UpstreamEvent(text_deltas=(("content", "never"),), final=True)
        finally:
            log.append("source closed")
    return gen()


async def owner_task_ticks() -> dict:
    """The §3.7 serving loop: one task owns the pending __anext__; keep-alives tick while it is pending."""
    log: list[str] = []
    it = bounded_events(source(log), BOUNDS)
    pending = asyncio.ensure_future(it.__anext__())
    ticks = 0
    while True:
        done, _ = await asyncio.wait({pending}, timeout=0.1)
        if done:
            break
        ticks += 1  # here: write ": keep-alive\n\n" through StreamTimeouts.send, head committed, no terminal
    ev = pending.result()
    pending = asyncio.ensure_future(it.__anext__())  # the next read; then the client leaves mid-wait
    await asyncio.sleep(0.05)
    pending.cancel()  # 1. cancel the owner task's pending read
    try:
        await pending  # 2. let it unwind
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    await it.aclose()  # 3. only now close the iterator (closes the attempt and the connection)
    return {"pattern": "owner task, cancel+await, then aclose", "ticks_before_event": ticks,
            "event": ev.text_deltas, "teardown_error": None, "log": log}


async def aclose_while_pending() -> dict:
    log: list[str] = []
    it = bounded_events(source(log), BOUNDS)
    pending = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0.05)
    err = None
    try:
        await it.aclose()  # wrong order: the generator is suspended inside the pending __anext__
    except RuntimeError as exc:
        err = str(exc)
    pending.cancel()
    try:
        await pending
    except (asyncio.CancelledError, StopAsyncIteration, RuntimeError):
        pass
    return {"pattern": "aclose while the read is pending", "teardown_error": err, "log": log}


if __name__ == "__main__":
    a = asyncio.run(owner_task_ticks())
    b = asyncio.run(aclose_while_pending())
    print(json.dumps(a, default=str))
    print(json.dumps(b, default=str))
    ok = a["event"] == (("content", "late"),) and a["ticks_before_event"] >= 2 and b["teardown_error"]
    print(json.dumps({"all_expectations_hold": bool(ok)}))
    raise SystemExit(0 if ok else 1)

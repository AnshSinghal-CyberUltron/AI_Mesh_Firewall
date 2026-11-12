"""Revision 7 (Plan Reviewer A, revision 6, blocker 2): gateway-side silence must never trip the
downstream idle timeout (`STREAM_IDLE_TIMEOUT`, 408, which blames the client).

    PYTHONPATH=<worktree>/gateway_v2:<worktree>/shared python idle7.py

Uses the REAL `gateway_v2.edge.stream_timeouts.StreamTimeouts` (this branch's develop code) with a fake
clock and a contract stub at the 20 ms p99 default (idle 3 s, write 1 s, inter-chunk 1 s), as Reviewer
A's `probes/idle_hold.py` does. Scenarios, each with and without the revision-7 keep-alive:
  1. INCREMENTAL, a customer-regex pass waits 3.2 s on the scan scheduler while upstream events keep
     arriving every 50 ms (A's case);
  2. the same after the upstream finished: the final pass waits 6 s, no upstream events at all;
  3. a hold-once rule undecided for 10 s of upstream deltas;
  4. a client that stops reading during the wait: the keep-alive write blocks, and the stream is cut
     by the WRITE bound (the client's fault, correctly), not hidden by the keep-alive.
Revision 7's rule: while the stream has bytes it cannot release because of the gateway's own
customer-regex work (a pass queued or running, or a rule undecided), the serving loop writes an SSE
comment line (`: keep-alive`) through `StreamTimeouts.send` every `idle_timeout_s() / 3` (1 s).
Part 2 feeds those comment lines to the OpenAI Python SDK's own SSE decoder (`openai._streaming`)
and checks it yields only the data events.
"""
import asyncio

from gateway_v2.edge.cancel import KillLatch
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts


class Contract:
    def idle_timeout_s(self) -> float:
        return 3.0

    def write_timeout_s(self) -> float:
        return 1.0

    def inter_chunk_timeout_s(self) -> float:
        return 1.0


KEEPALIVE_S = Contract().idle_timeout_s() / 3


async def scenario(name: str, wait_s: float, upstream_every: float | None, keepalive: bool,
                   client_stops: bool = False) -> str:
    now = [0.0]
    latch = KillLatch()
    st = StreamTimeouts(contract=Contract(), kill_latch=latch, clock=lambda: now[0])

    async def ok() -> None:
        return None

    async def blocked() -> None:  # a client that stopped reading: the write never completes
        await asyncio.sleep(3600)

    await st.send(ok)  # the first release
    step = 0.05
    t = 0.0
    last_ka = 0.0
    try:
        while t < wait_s:
            t += step
            now[0] += step
            if upstream_every is not None:
                await st.next_event(ok())
            if keepalive and t - last_ka >= KEEPALIVE_S:
                last_ka = t
                await st.send(blocked if client_stops else ok)
        await st.send(ok)  # the pass finished: the held bytes are released
        return "completed"
    except StreamTimeout as e:
        return f"cut: {e.code} at {t:.2f} s"


async def main() -> None:
    rows = [
        ("1. pass waits 3.2 s, upstream every 50 ms", 3.2, 0.05, False),
        ("2. final pass waits 6 s, upstream finished", 6.0, None, False),
        ("3. rule undecided 10 s, upstream every 50 ms", 10.0, 0.05, False),
    ]
    print(f"{'scenario':48} {'HEAD (no keep-alive)':40} {'revision 7 (keep-alive every 1 s)':40}")
    for name, wait, up, _ in rows:
        head = await scenario(name, wait, up, keepalive=False)
        rev7 = await scenario(name, wait, up, keepalive=True)
        print(f"{name:48} {head:40} {rev7:40}")
    # 4. the client stops reading during a 6 s wait: the keep-alive write blocks
    loop_task = asyncio.wait_for(scenario("4", 6.0, None, keepalive=True, client_stops=True), timeout=5)
    try:
        res = await loop_task
    except TimeoutError:
        res = "hung"
    print(f"{'4. client stops reading during a 6 s wait':48} {'(no write to block)':40} {res:40}")


asyncio.run(main())

# Part 2: the OpenAI Python SDK's SSE decoder ignores comment lines.
from openai._streaming import SSEDecoder  # noqa: E402

wire = (b": keep-alive\n\n"
        b'data: {"id":"c1","choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b": keep-alive\n\n: keep-alive\n\n"
        b'data: {"id":"c1","choices":[{"delta":{"content":"lo"}}]}\n\n'
        b": keep-alive\n\n"
        b"data: [DONE]\n\n")
events = list(SSEDecoder().iter_bytes(iter([wire[i:i + 7] for i in range(0, len(wire), 7)])))
import openai  # noqa: E402

print(f"\nopenai {openai.__version__} SSEDecoder over a stream with 4 keep-alive comments (7-byte chunks): "
      f"{len(events)} events: {[e.data[:30] for e in events]}")

"""De-blocking probe — an A/B FastAPI app that isolates the exact mechanism the
gateway relies on so item 14 (a slow request must not stall concurrent requests on
the same worker) can be proven on ONE uvicorn worker.

Three endpoints, all reached on the same single event loop:
  * GET /fast            — returns immediately (the "other" concurrent requests).
  * GET /block?ms=N      — `time.sleep(N/1000)` INLINE on the loop (the ANTI-pattern:
                           blocks the worker; every concurrent /fast queues behind it).
  * GET /offload?ms=N    — `await asyncio.to_thread(time.sleep, N/1000)` (EXACTLY what
                           proxy_chat does for the CPU scan: the blocking call runs on
                           a thread, the loop stays free to serve /fast).

Run under the gateway image's own uvicorn so the runtime matches production:
  uvicorn deblock_probe:app --host 0.0.0.0 --port 8401 --workers 1
"""

from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI

app = FastAPI()


@app.get("/fast")
async def fast():
    return {"ok": True}


@app.get("/block")
async def block(ms: int = 500):
    # ANTI-pattern: synchronous sleep directly on the event loop.
    time.sleep(ms / 1000.0)
    return {"blocked_ms": ms}


@app.get("/offload")
async def offload(ms: int = 500):
    # The gateway pattern: push the blocking call to a worker thread.
    await asyncio.to_thread(time.sleep, ms / 1000.0)
    return {"offloaded_ms": ms}

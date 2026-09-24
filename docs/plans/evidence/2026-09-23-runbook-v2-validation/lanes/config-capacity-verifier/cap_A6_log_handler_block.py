"""Measure: does the REAL RedisLogPublisher block the asyncio event loop per log line?

Setup mirrors main.py:6716-6730 (baseline) exactly:
  * root logging configured like _configure_logging() with GATEWAY_LOG_LEVEL=INFO
    (logging.basicConfig(level=INFO, handlers=[StreamHandler], force=True))
  * RedisLogPublisher(redis_url=..., service_name="Gateway") with Formatter("%(message)s")
    attached to logging.getLogger("gateway"), and gateway logger setLevel(DEBUG)
The Redis server is a tiny RESP responder on a background THREAD (so it is not
blocked by the loop under test) that delays each PUBLISH reply by LATENCY_MS,
emulating network/Redis latency. The real sync redis-py client does a real socket
round trip. A ticker coroutine records its wake-ups every 5 ms; if emit() blocks
the loop, the ticker is starved for the whole emit duration.
"""
import asyncio
import io
import logging
import socketserver
import sys
import threading
import time

from ai_mesh_shared.redis_log_handler import RedisLogPublisher

LATENCY_MS = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
N_LINES = int(sys.argv[2]) if len(sys.argv) > 2 else 20
PUBLISHES = []


class RESPHandler(socketserver.StreamRequestHandler):
    def _read_command(self):
        line = self.rfile.readline()
        if not line:
            return None
        if not line.startswith(b"*"):
            return [line.strip()]
        n = int(line[1:])
        parts = []
        for _ in range(n):
            ln = int(self.rfile.readline()[1:])
            parts.append(self.rfile.read(ln + 2)[:-2])
        return parts

    def handle(self):
        while True:
            cmd = self._read_command()
            if cmd is None:
                return
            name = cmd[0].upper()
            if name == b"PUBLISH":
                time.sleep(LATENCY_MS / 1000.0)
                PUBLISHES.append((time.perf_counter(), cmd[2][:60]))
                self.wfile.write(b":0\r\n")
            elif name == b"HELLO":  # redis-py 8 defaults to RESP3: reply with a RESP3 map
                self.wfile.write(b"%1\r\n$5\r\nproto\r\n:3\r\n")
            elif name == b"PING":
                self.wfile.write(b"+PONG\r\n")
            else:  # CLIENT SETINFO etc.
                self.wfile.write(b"+OK\r\n")
            self.wfile.flush()


class Srv(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


srv = Srv(("127.0.0.1", 0), RESPHandler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

# --- replicate _configure_logging() with GATEWAY_LOG_LEVEL=INFO (stdout sink captured) ---
sink = io.StringIO()
root_handler = logging.StreamHandler(sink)
root_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[root_handler], force=True)

# --- replicate main.py:6716-6730 ---
pub = RedisLogPublisher(redis_url=f"redis://127.0.0.1:{port}/0", service_name="Gateway")
pub.setFormatter(logging.Formatter("%(message)s"))
gateway_logger = logging.getLogger("gateway")
gateway_logger.addHandler(pub)
gateway_logger.setLevel(logging.DEBUG)

child = logging.getLogger("gateway.scanner")  # a real child logger name used by scanner.py
print(f"root level={logging.getLevelName(logging.getLogger().level)}  gateway level={logging.getLevelName(gateway_logger.level)}  "
      f"child effective={logging.getLevelName(child.getEffectiveLevel())}  child.isEnabledFor(DEBUG)={child.isEnabledFor(logging.DEBUG)}")


async def main():
    ticks = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    async def request_handler():
        # what a coroutine on the request path does: log N debug lines
        t0 = time.perf_counter()
        for i in range(N_LINES):
            child.debug("scan stage %d finished", i)
        return time.perf_counter() - t0

    # warm the connection (first publish also pays connect)
    child.debug("warmup")
    t_task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    n_before = len(ticks)
    t_start = time.perf_counter()
    elapsed = await request_handler()
    await asyncio.sleep(0.05)
    stop.set()
    await t_task
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    starved = [g for g in gaps if g > 0.1]
    last_before = ticks[n_before - 1]
    first_after = next(t for t in ticks if t > t_start)
    print(f"injected Redis PUBLISH latency = {LATENCY_MS:.0f} ms; debug lines logged from a coroutine = {N_LINES}")
    print(f"wall time of the {N_LINES} logger.debug() calls = {elapsed*1000:.1f} ms "
          f"(expected if blocking ~ N x latency = {N_LINES*LATENCY_MS:.0f} ms)")
    print(f"per-line cost = {elapsed*1000/N_LINES:.1f} ms")
    print(f"ticker (5 ms period) max gap = {max(gaps)*1000:.1f} ms; gaps > 100 ms: {len(starved)}; "
          f"ticks recorded during the logging window: "
          f"{sum(1 for t in ticks if t_start < t < t_start + elapsed)}; "
          f"ticker gap spanning the logging window = {(first_after - last_before)*1000:.1f} ms")
    print(f"PUBLISH commands received by server: {len(PUBLISHES)} (expected 1 warmup + {N_LINES} = {N_LINES + 1})")
    print(f"DEBUG lines also written to root stdout handler despite GATEWAY_LOG_LEVEL=INFO: "
          f"{sink.getvalue().count('[DEBUG] gateway.scanner')}")


asyncio.run(main())
srv.shutdown()

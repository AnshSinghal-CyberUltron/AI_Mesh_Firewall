"""§10.1.5 row 5 / GW06: same Redis fault, opposite postures.

Executes the REAL RateLimiter (rate_limiter.py, own pool socket_timeout=1.0),
middleware.validate_api_key (AuthMiddleware pool socket_timeout=2.0, middleware.py:316)
and CircuitBreaker.check (shared REDIS_CLIENT socket_timeout=3, main.py:6487) against:
  (a) a dead Redis (connection refused),
  (b) a RESP server answering every command after 1.5 s (between the 1.0 s and 2.0 s timeouts),
  (c) the same server at 2.5 s (between 2.0 s and 3 s).
Each client is built with the SAME timeouts the gateway uses for that component.
"""
import asyncio
import json
import socket
import socketserver
import threading
import time

import redis.asyncio as aioredis

LAT = [0.0]


class RESP(socketserver.StreamRequestHandler):
    disable_nagle_algorithm = True

    def _cmd(self):
        line = self.rfile.readline()
        if not line:
            return None
        n = int(line[1:])
        parts = []
        for _ in range(n):
            ln = int(self.rfile.readline()[1:])
            parts.append(self.rfile.read(ln + 2)[:-2].decode())
        return parts

    def handle(self):
        try:
            while True:
                c = self._cmd()
                if c is None:
                    return
                name = c[0].upper()
                w = self.wfile.write
                if name in ("HELLO",):
                    w(b"%1\r\n$5\r\nproto\r\n:3\r\n")
                elif name == "CLIENT":
                    w(b"+OK\r\n")
                else:
                    time.sleep(LAT[0])
                    if name == "GET" and c[1].startswith("auth:apikey:"):
                        body = json.dumps({"key_id": 1, "user_id": 1, "project_id": 1, "org_slug": "acme",
                                           "is_active": True, "prefix": "zs_test1"}).encode()
                        w(b"$%d\r\n%s\r\n" % (len(body), body))
                    elif name == "GET":
                        w(b"_\r\n")
                    elif name == "EVAL":
                        w(b"*2\r\n:1\r\n:20\r\n")
                    else:
                        w(b":1\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


class Srv(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


srv = Srv(("127.0.0.1", 0), RESP)
LIVE_URL = f"redis://127.0.0.1:{srv.server_address[1]}/0"
threading.Thread(target=srv.serve_forever, daemon=True).start()
s = socket.socket(); s.bind(("127.0.0.1", 0)); DEAD_URL = f"redis://127.0.0.1:{s.getsockname()[1]}/0"; s.close()

from middleware import validate_api_key  # noqa: E402
from rate_limiter import RateLimiter  # noqa: E402
from circuit_breaker import CircuitBreaker  # noqa: E402


async def probe(label, url):
    auth_client = aioredis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0,
                                          socket_connect_timeout=1.0)          # middleware.py:311-317
    shared = aioredis.Redis.from_url(url, decode_responses=True, socket_timeout=3,
                                     socket_connect_timeout=2)                   # main.py:6484-6489
    rl = RateLimiter(redis_url=url)                                              # rate_limiter.py:41-48
    cb = CircuitBreaker(redis_client=shared)
    out = {}
    t = time.perf_counter(); ctx, err = await validate_api_key("zs_test1_" + "x" * 32, auth_client)
    out["auth"] = (f"{err['status_code']} {err['error']}" if err else "ok", round(time.perf_counter() - t, 2))
    t = time.perf_counter(); out["key TPM (rate_limiter:116)"] = (await rl.check_rate_limit("h", 1000), round(time.perf_counter() - t, 2))
    t = time.perf_counter(); out["model RPM (rate_limiter:169)"] = (await rl.check_model_rate_limit("m", 10, "acme"), round(time.perf_counter() - t, 2))
    t = time.perf_counter(); out["org TPM (rate_limiter:230)"] = (await rl.check_org_rate_limit("acme", 1000), round(time.perf_counter() - t, 2))
    t = time.perf_counter(); st = await cb.check("gpt-4o-mini")
    out["circuit_breaker.check (:397-400)"] = (f"should_block={st.should_block} state={st.state.value}", round(time.perf_counter() - t, 2))
    print(f"--- {label}")
    for k, (v, secs) in out.items():
        print(f"    {k:34} -> {v}   [{secs}s]")


async def main():
    await probe("(a) Redis DOWN (connection refused)", DEAD_URL)
    LAT[0] = 0.0
    await probe("(0) Redis healthy (control)", LIVE_URL)
    LAT[0] = 1.5
    await probe("(b) Redis SLOW 1.5 s/command", LIVE_URL)
    LAT[0] = 2.5
    await probe("(c) Redis SLOW 2.5 s/command", LIVE_URL)


asyncio.run(main())
srv.shutdown()

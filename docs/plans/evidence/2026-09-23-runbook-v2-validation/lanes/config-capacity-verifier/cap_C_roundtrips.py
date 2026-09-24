"""Count Redis round trips for the v1 chat prefix by EXECUTING the real component
functions, in main.py's order (baseline line numbers):

  AuthMiddleware -> middleware.validate_api_key          (middleware.py:205)
  kill-switch    -> kill_switch.check_kill_switch        (main.py:8012 -> kill_switch.py:63-66)
  model state    -> model_state.check_model_state        (main.py:8150 -> model_state.py:56)
  burst + RPM    -> rate_limit_enforcement.enforce_org_burst_rpm
                    (the shared extraction whose docstring says it mirrors the proxy_chat
                    inline block main.py:8361-8364 / 8403-8406 exactly)
  org TPM        -> RateLimiter.check_org_rate_limit     (main.py:8479, only if org_tpm_limit)
  key TPM        -> RateLimiter.check_rate_limit         (main.py:8514, only if rate_limit_tpm)

Server: a RESP3 responder thread with injected per-round-trip latency. A round trip is
counted client-side as one AbstractConnection.send_packed_command() call (redis-py sends
a whole pipeline/MULTI in one packed write and a single command in one write).
Also counts the sync RedisLogPublisher publishes the same functions trigger via logging.
"""
import asyncio
import json
import logging
import socketserver
import sys
import threading
import time

import redis.asyncio as aioredis
from redis.asyncio import connection as aconn

LAT_MS = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
SERVER_CMDS = []


class RESP(socketserver.StreamRequestHandler):
    disable_nagle_algorithm = True  # avoid Nagle/delayed-ACK artifacts from the fake server
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
        in_multi = False
        queued = []
        while True:
            c = self._cmd()
            if c is None:
                return
            name = c[0].upper()
            SERVER_CMDS.append(name)
            w = self.wfile.write
            if name == "HELLO":
                w(b"%1\r\n$5\r\nproto\r\n:3\r\n")
            elif name == "CLIENT":
                w(b"+OK\r\n")
            elif name == "MULTI":
                in_multi, queued = True, []
                w(b"+OK\r\n")
            elif in_multi and name != "EXEC":
                queued.append(name)
                w(b"+QUEUED\r\n")
            elif name == "EXEC":
                time.sleep(LAT_MS / 1000)
                in_multi = False
                w(b"*%d\r\n" % len(queued) + b":1\r\n" * len(queued))
            else:
                time.sleep(LAT_MS / 1000) if name != "PUBLISH" else None
                if name == "GET":
                    if c[1].startswith("auth:apikey:"):
                        body = json.dumps({"key_id": 1, "user_id": 1, "project_id": 1, "org_slug": "acme",
                                           "is_active": True, "prefix": "zs_test1"}).encode()
                        w(b"$%d\r\n%s\r\n" % (len(body), body))
                    else:
                        w(b"_\r\n")
                elif name == "EVAL":
                    w(b"*2\r\n:1\r\n:20\r\n")
                elif name == "PUBLISH":
                    w(b":0\r\n")
                else:
                    w(b":1\r\n")
            self.wfile.flush()


class Srv(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


srv = Srv(("127.0.0.1", 0), RESP)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"redis://127.0.0.1:{PORT}/0"

ROUND_TRIPS = []
_orig_send = aconn.AbstractConnection.send_packed_command


async def _counting_send(self, command, check_health=True):
    ROUND_TRIPS.append(STAGE[0])
    return await _orig_send(self, command, check_health)


aconn.AbstractConnection.send_packed_command = _counting_send
STAGE = ["setup"]

# Wire the sync log publisher exactly like main.py:6716-6730 so log-driven publishes are visible.
from ai_mesh_shared.redis_log_handler import RedisLogPublisher  # noqa: E402

logging.basicConfig(level=logging.INFO, force=True, handlers=[logging.NullHandler()])
pub = RedisLogPublisher(redis_url=URL, service_name="Gateway")
g = logging.getLogger("gateway")
g.addHandler(pub)
g.setLevel(logging.DEBUG)

from middleware import validate_api_key  # noqa: E402
from kill_switch import check_kill_switch  # noqa: E402
from model_state import check_model_state  # noqa: E402
from rate_limit_enforcement import enforce_org_burst_rpm  # noqa: E402
from rate_limiter import RateLimiter  # noqa: E402


class _Sync:
    def get_config(self, slug):
        return {"rate_limit_enabled": True, "enforcement_mode": "block", "burst_limit": 150,
                "requests_per_minute": 1000, "org_tpm_limit": 100000}


async def main():
    client = aioredis.Redis.from_url(URL, decode_responses=True)
    rl = RateLimiter(redis_url=URL)
    await client.ping()
    # warm both connection pools (HELLO/CLIENT SETINFO handshakes) so counts are steady-state
    await rl.check_org_rate_limit("warm", 1_000_000)
    await validate_api_key("zs_test1_" + "x" * 32, client)
    ROUND_TRIPS.clear()
    SERVER_CMDS.clear()
    pubs_before = sum(1 for _ in [])
    t0 = time.perf_counter()
    timings = {}

    async def stage(name, coro):
        STAGE[0] = name
        s = time.perf_counter()
        r = await coro
        timings[name] = (time.perf_counter() - s) * 1000
        return r

    ctx, err = await stage("auth GET (validate_api_key)", validate_api_key("zs_test1_" + "x" * 32, client))
    assert err is None, err
    await stage("kill-switch pipeline", check_kill_switch(client, "gpt-4o-mini", org_slug="acme", key_prefix=ctx.prefix))
    await stage("model_state GET (untimed)", check_model_state(client, "gpt-4o-mini", org_slug="acme"))
    r = await stage("burst MULTI + RPM MULTI", enforce_org_burst_rpm(
        ctx, redis_client=client, config_sync=_Sync(), gateway_config={}, metrics={},
        emit_telemetry=lambda **k: None, event_type="chat"))
    assert r is None
    await stage("org TPM EVAL", rl.check_org_rate_limit("acme", 100000))
    await stage("key TPM EVAL", rl.check_rate_limit(ctx.key_hash, 100000))
    total = (time.perf_counter() - t0) * 1000
    await asyncio.sleep(0.2)

    from collections import Counter
    per_stage = Counter(ROUND_TRIPS)
    print(f"injected server latency per round trip = {LAT_MS} ms (PUBLISH not delayed)")
    for name, ms in timings.items():
        print(f"  {name:32s} async round trips = {per_stage.get(name, 0)}   wall = {ms:6.1f} ms")
    print(f"TOTAL async Redis round trips in prefix = {sum(per_stage.values())}; wall = {total:.1f} ms "
          f"(serial: each stage awaited before the next)")
    c = Counter(SERVER_CMDS)
    print("server-side commands:", dict(c))
    print(f"sync PUBLISH (log lines -> RedisLogPublisher, blocking the loop) during prefix = {c.get('PUBLISH', 0)}")


asyncio.run(main())
srv.shutdown()

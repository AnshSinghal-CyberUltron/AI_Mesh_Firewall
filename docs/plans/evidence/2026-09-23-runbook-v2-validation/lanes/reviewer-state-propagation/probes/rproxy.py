"""Controllable TCP fault proxy in front of Redis (reviewer-state-propagation).

  python rproxy.py --listen 26380 --backend 26379 --control 26390

Control (HTTP GET on the control port):
  /mode?m=pass                      forward immediately
  /mode?m=delay&ms=200              delay every chunk (both directions) by ms
  /blackhole_existing               stop forwarding on every CURRENT connection (sockets stay open:
                                    a half-open peer, as after a store failover); new ones are normal
  /reset_existing                   close every current connection (clean failover: RST/EOF)
  /backend?port=26381               new connections go to this backend
  /delay_reply?match=HGET&match2=rv:keys&ms=300
                                    delay the NEXT server->client chunk on a connection whose last
                                    client->server chunk contained both byte strings (one-shot per hit)
  /stats                            JSON counters
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from aiohttp import web


class Conn:
    def __init__(self, cid: int) -> None:
        self.cid = cid
        self.blackholed = False
        self.delay_next_reply_ms = 0.0
        self.writers: list[asyncio.StreamWriter] = []


class Proxy:
    def __init__(self, a: argparse.Namespace) -> None:
        self.backend = a.backend
        self.mode = "pass"
        self.delay_ms = 0.0
        self.conns: dict[int, Conn] = {}
        self.next_id = 0
        self.sel: tuple[bytes, bytes, float] | None = None
        self.sel_left = -1
        self.stats = {"accepted": 0, "selective_delays": 0, "blackholed": 0, "reset": 0}

    async def handle(self, cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        self.next_id += 1
        c = Conn(self.next_id)
        self.conns[c.cid] = c
        self.stats["accepted"] += 1
        try:
            sr, sw = await asyncio.open_connection("127.0.0.1", self.backend)
        except OSError:
            cw.close()
            self.conns.pop(c.cid, None)
            return
        c.writers = [cw, sw]

        async def pump(r: asyncio.StreamReader, w: asyncio.StreamWriter, upstream: bool) -> None:
            try:
                while True:
                    data = await r.read(65536)
                    if not data:
                        break
                    while c.blackholed:  # hold forever (half-open): never forward, never close
                        await asyncio.sleep(3600)
                    if (upstream and self.sel is not None and self.sel_left != 0
                            and self.sel[0] in data and self.sel[1] in data):
                        c.delay_next_reply_ms = self.sel[2]
                        self.sel_left -= 1
                    if not upstream and c.delay_next_reply_ms:
                        d, c.delay_next_reply_ms = c.delay_next_reply_ms, 0.0
                        self.stats["selective_delays"] += 1
                        await asyncio.sleep(d / 1000.0)
                    if self.mode == "delay" and self.delay_ms:
                        await asyncio.sleep(self.delay_ms / 1000.0)
                    w.write(data)
                    await w.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                if not c.blackholed:
                    try:
                        w.close()
                    except Exception:
                        pass

        await asyncio.gather(pump(cr, sw, True), pump(sr, cw, False))
        self.conns.pop(c.cid, None)

    async def control(self, req: web.Request) -> web.Response:
        p = req.path
        q = req.query
        if p == "/mode":
            self.mode = q.get("m", "pass")
            self.delay_ms = float(q.get("ms", "0"))
        elif p == "/blackhole_existing":
            for c in self.conns.values():
                c.blackholed = True
                self.stats["blackholed"] += 1
        elif p == "/reset_existing":
            for c in list(self.conns.values()):
                for w in c.writers:
                    try:
                        w.transport.abort()
                    except Exception:
                        pass
                self.stats["reset"] += 1
        elif p == "/backend":
            self.backend = int(q["port"])
        elif p == "/delay_reply":
            self.sel = (q["match"].encode(), q.get("match2", q["match"]).encode(), float(q["ms"]))
            self.sel_left = int(q.get("count", "-1"))  # -1 = unlimited
        elif p == "/delay_reply_off":
            self.sel = None
        body = {"t": time.time(), "mode": self.mode, "delay_ms": self.delay_ms, "backend": self.backend,
                "live_conns": len(self.conns), "blackholed_conns": sum(c.blackholed for c in self.conns.values()),
                "selective": None if self.sel is None else [self.sel[0].decode(), self.sel[1].decode(), self.sel[2]],
                **self.stats}
        return web.json_response(body)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, required=True)
    ap.add_argument("--backend", type=int, required=True)
    ap.add_argument("--control", type=int, required=True)
    a = ap.parse_args()
    p = Proxy(a)
    srv = await asyncio.start_server(p.handle, "127.0.0.1", a.listen)
    app = web.Application()
    app.router.add_get("/{tail:.*}", p.control)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", a.control).start()
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())

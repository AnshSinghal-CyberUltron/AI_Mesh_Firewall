"""Is BaseHTTPMiddleware's p99.9 tail GC? In-process ASGI calls (blocking receive like a real server, responses
validated, uvloop), 100k sequential requests per cell after 2k warm-up. Every GC pause is timed via gc.callbacks.
Cells: variant (none|base4) x gc mode (on | freeze-after-warmup | disabled). Attribution only, not a latency claim."""
import asyncio, gc, json, os, sys, time
sys.path.insert(0, os.path.expanduser("~"))
BODY = open(os.path.expanduser("~/body4k.json"), "rb").read()
def build(v):
    os.environ["MW_VARIANT"] = v
    import importlib, mw_app; importlib.reload(mw_app); return mw_app.app
async def call(app):
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "POST",
             "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions", "query_string": b"", "root_path": "",
             "scheme": "http", "headers": [(b"content-type", b"application/json"), (b"host", b"x"),
             (b"content-length", str(len(BODY)).encode())], "client": ("1.2.3.4", 1234), "server": ("x", 80)}
    done = asyncio.Event(); sent = []; body = [BODY]
    async def receive():
        if body: return {"type": "http.request", "body": body.pop(), "more_body": False}
        await done.wait(); return {"type": "http.disconnect"}
    async def send(m):
        sent.append(m)
        if m["type"] == "http.response.body" and not m.get("more_body"): done.set()
    await app(scope, receive, send)
    assert sent[0]["status"] == 200
PAUSES = []; _t = {}
def cb(phase, info):
    if phase == "start": _t["t"] = time.perf_counter()
    else: PAUSES.append((info["generation"], (time.perf_counter() - _t["t"]) * 1e6))
gc.callbacks.append(cb)
def pct(xs, q): xs = sorted(xs); return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1)
async def main():
    res = []
    for v in ("none", "base4"):
        for mode in ("on", "freeze", "off"):
            gc.enable(); gc.collect(); app = build(v)
            for _ in range(2000): await call(app)
            if mode == "freeze": gc.freeze()
            if mode == "off": gc.disable()
            PAUSES.clear(); lat = []
            for _ in range(100_000):
                t = time.perf_counter(); await call(app); lat.append((time.perf_counter() - t) * 1e6)
            g2 = [p for g, p in PAUSES if g == 2]
            r = {"variant": v, "gc": mode, "n": len(lat), "p50_us": pct(lat, .5), "p99_us": pct(lat, .99), "p999_us": pct(lat, .999),
                 "max_us": round(max(lat), 1), "gc_pauses": len(PAUSES), "gen2_pauses": len(g2),
                 "gen2_pause_p50_us": pct(g2, .5) if g2 else None, "gen2_pause_max_us": round(max(g2), 1) if g2 else None,
                 "gen01_pause_max_us": round(max([p for g, p in PAUSES if g < 2] or [0]), 1), "tracked_objects": len(gc.get_objects())}
            print(json.dumps(r), flush=True); res.append(r)
            gc.unfreeze(); gc.enable()
import uvloop; uvloop.run(main())

"""cProfile of the in-process ASGI call path (E7 harness shape, but receive() blocks after the body like a
real server and every response is validated) for variants none/base4. Attribution only, not a latency claim."""
import asyncio, cProfile, pstats, io, os, sys, time
sys.path.insert(0, os.path.expanduser("~"))
BODY = open(os.path.expanduser("~/body4k.json"), "rb").read()
def build(v):
    os.environ["MW_VARIANT"] = v
    import importlib, mw_app; importlib.reload(mw_app); return mw_app.app
async def call(app):
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
             "query_string": b"", "root_path": "", "scheme": "http",
             "headers": [(b"content-type", b"application/json"), (b"host", b"x"), (b"content-length", str(len(BODY)).encode())],
             "client": ("1.2.3.4", 1234), "server": ("x", 80)}
    done = asyncio.Event(); sent = []; body = [BODY]
    async def receive():
        if body: return {"type": "http.request", "body": body.pop(), "more_body": False}
        await done.wait(); return {"type": "http.disconnect"}
    async def send(m):
        sent.append(m)
        if m["type"] == "http.response.body" and not m.get("more_body"): done.set()
    await app(scope, receive, send)
    assert sent[0]["status"] == 200 and sent[-1]["type"] == "http.response.body", sent
async def main():
    import uvloop
    for v in ("none", "base4"):
        app = build(v)
        for _ in range(300): await call(app)
        n = 3000; t = time.perf_counter()
        for _ in range(n): await call(app)
        print(f"{v}: {(time.perf_counter()-t)/n*1e3:.4f} ms/request (sequential, blocking receive, uvloop)")
    pr = cProfile.Profile(); pr.enable()
    for _ in range(2000): await call(app)
    pr.disable(); s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(22); print(s.getvalue()[:6000])
import uvloop; uvloop.run(main())

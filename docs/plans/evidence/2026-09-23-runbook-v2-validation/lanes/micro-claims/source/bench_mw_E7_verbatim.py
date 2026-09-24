"""E7: ASGI middleware-stack overhead. 4x BaseHTTPMiddleware + 2 pure-ASGI, as in main.py."""
import asyncio, time, statistics, sys
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
import starlette; print("starlette", starlette.__version__)

async def ep(request):
    await request.json()
    return JSONResponse({"ok": True, "id": "chatcmpl-x"})

def build(n_basehttp, cors=False, pure=0):
    app = Starlette(routes=[Route("/v1/chat/completions", ep, methods=["POST"])])
    for i in range(n_basehttp):
        class M(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                resp = await call_next(request)
                resp.headers["x-mw"] = "1"
                return resp
        app.add_middleware(M)
    if cors: app.add_middleware(CORSMiddleware, allow_origins=["*"])
    inner = app
    for _ in range(pure):
        class Pure:
            def __init__(self, a): self.a=a
            async def __call__(self, scope, receive, send):
                if scope["type"]!="http": return await self.a(scope,receive,send)
                return await self.a(scope,receive,send)
        inner = Pure(inner)
    return inner

BODY = b'{"model":"gpt-4o-mini","messages":[{"role":"user","content":"%s"}],"max_tokens":1024}' % (b"x"*3900)

async def call(app):
    scope={"type":"http","asgi":{"version":"3.0"},"http_version":"1.1","method":"POST",
           "path":"/v1/chat/completions","raw_path":b"/v1/chat/completions","query_string":b"",
           "root_path":"","scheme":"http","headers":[(b"content-type",b"application/json"),
           (b"host",b"x"),(b"content-length",str(len(BODY)).encode())],
           "client":("1.2.3.4",1234),"server":("x",80),"app":None}
    sent=[]
    body_left=[BODY]
    async def receive():
        if body_left: return {"type":"http.request","body":body_left.pop(),"more_body":False}
        return {"type":"http.disconnect"}
    async def send(m): sent.append(m)
    await app(scope, receive, send)
    return sent

async def main():
    async def bench(app,n=3000,warm=300):
        for _ in range(warm): await call(app)
        xs=[]
        for _ in range(n):
            t=time.perf_counter(); await call(app); xs.append((time.perf_counter()-t)*1e3)
        xs.sort(); return xs[len(xs)//2], xs[int(n*.99)]
    base=build(0)
    b50,b99 = await bench(base)
    print(f"{'bare route (json parse + JSONResponse)':52s} p50={b50:7.4f}ms p99={b99:7.4f}ms")
    for n in (1,2,3,4):
        a=build(n)
        m50,m99=await bench(a)
        print(f"{f'+ {n}x BaseHTTPMiddleware':52s} p50={m50:7.4f}ms p99={m99:7.4f}ms  "
              f"delta_p50={m50-b50:+7.4f}ms ({(m50-b50)/n:.4f} ms each)")
    a=build(4, cors=True, pure=1)
    m50,m99=await bench(a)
    print(f"{'main.py shape: 4x BaseHTTP + CORS + 1 pure ASGI':52s} p50={m50:7.4f}ms p99={m99:7.4f}ms  "
          f"delta_p50={m50-b50:+7.4f}ms")
asyncio.run(main())

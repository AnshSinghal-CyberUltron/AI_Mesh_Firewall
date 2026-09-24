"""Claim 1 SUT: minimal Starlette app, identical handler, four middleware variants (env MW_VARIANT).

  none    : no user middleware
  base4   : 4 x BaseHTTPMiddleware, trivial pass-through (return await call_next(request))
  pure4   : 4 x pure-ASGI pass-through (await self.app(scope, receive, send))
  v1stack : the TYPES/COUNT of v1's real stack (gateway/ai_mesh_gateway/main.py @HEAD, outer->inner):
            CORSMiddleware (real Starlette class, v1's exact config, main.py:507)
            -> 4 x @app.middleware("http") == BaseHTTPMiddleware (main.py:228,257,345,375; pass-through here)
            -> 1 x pure-ASGI (AuthMiddleware type, main.py:138 / middleware.py:278; pass-through here)
The handler parses the JSON request body (like a chat handler) and returns a small OpenAI-shaped
chat.completion JSON (so the open-loop generator can validate it). Starlette 1.2.0 = v1's pinned version.
"""
import os
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

COMPLETION = {
    "id": "chatcmpl-rvmicro", "object": "chat.completion", "created": 1700000000, "model": "rv-micro",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


async def chat(request):
    await request.json()
    return JSONResponse(COMPLETION)


class PassBase(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return await call_next(request)


class PassPure:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


# v1's CORS configuration verbatim (main.py:470-525): no Origin header on API traffic -> pass-through path
_LOCAL_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|\[::1\]|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)
V1_CORS = dict(
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8180",
                   "http://127.0.0.1:8180", "http://localhost:3000"],
    allow_origin_regex=_LOCAL_ORIGIN_REGEX, allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["accept", "content-type", "authorization", "x-user-id", "x-endpoint-id", "x-agent-data"],
    expose_headers=["content-type", "content-length", "x-request-id"],
)

VARIANTS = {
    "none": [],
    "base4": [Middleware(PassBase) for _ in range(4)],
    "pure4": [Middleware(PassPure) for _ in range(4)],
    "v1stack": [Middleware(CORSMiddleware, **V1_CORS)] + [Middleware(PassBase) for _ in range(4)]
               + [Middleware(PassPure)],
}
VARIANT = os.environ.get("MW_VARIANT", "none")
app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"])], middleware=VARIANTS[VARIANT])

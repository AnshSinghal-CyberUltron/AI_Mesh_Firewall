"""Phase-6 LIVE-uvicorn harness: serves the FIXED integration gateway over a real
HTTP socket with the SAME stubbed backend as the in-process conformance harness
(no redis/mongo/upstream needed). Lets the stock SDK hit http://127.0.0.1:8399/v1
to exercise real SSE framing, client disconnect, and wire headers.

Run: cd <integration>/gateway && PYTHONPATH="$PWD:$PWD/ai_mesh_gateway:$PWD/../shared" \
       <main venv>/python _live_server.py
"""
import hashlib
import json

import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock

from ai_mesh_gateway import main as gm
from ai_mesh_gateway import middleware as mw
from ai_mesh_gateway.scanner import InputScanner
from ai_mesh_gateway.tests import test_openai_sdk_compat as T

# ── auth (fakeredis) — seeded in a startup hook so it binds to uvicorn's loop ──
auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

async def _get_redis(self):
    return auth_redis

mw.AuthMiddleware._get_redis = _get_redis

# ── stub the gateway singletons (identical to tests._make_sdk_app) ──
cs = MagicMock()
cs.get_config = MagicMock(return_value=dict(T.TEST_CONFIG))
cs.get_model_routing = MagicMock(return_value=[dict(T.TEST_MODEL), dict(T.EMBED_MODEL)])
cs.reload_models_now = AsyncMock()

lr = MagicMock()
lr.acompletion = AsyncMock(side_effect=T._fake_completion)
lr.acompletion_stream = T._fake_stream
async def _emb_or_raise(body, *a, **k):
    # Phase-6 live trigger: input "__raise500__" makes aembedding raise so we can prove
    # the escaped-exception fix (nested 500 + x-request-id) over the real socket.
    inp = body.get("input")
    if inp == "__raise500__" or (isinstance(inp, list) and "__raise500__" in inp):
        raise RuntimeError("embedding provider connection failed (live test trigger)")
    return await T._fake_embedding(body, *a, **k)
lr.aembedding = AsyncMock(side_effect=_emb_or_raise)
lr.get_model_list = MagicMock(return_value=[
    {"id": "gpt-4o-mini", "object": "model", "created": 1704067200, "owned_by": "openai"},
])

gm.CONFIG = dict(T.TEST_CONFIG)
gm.CONFIG_SYNC = cs
gm.LLM_ROUTER = lr
gm.INPUT_SCANNER = InputScanner(thread_pool_size=2)
gm.AGENT_ID = None
gm.POLICY_SYNC = None
gm.RATE_LIMITER = None
gm.CIRCUIT_BREAKER = None
gm.REDIS_CLIENT = auth_redis          # redis-backed so store/responses cells work
gm.TELEMETRY = None
gm.OUTPUT_GUARD = None
gm._emit_telemetry = lambda **_kw: None
gm._audit_fire_and_forget = lambda **_kw: None

app = gm.app
# Neutralize the real startup/shutdown (they connect to redis/policy/mongo); replace
# with a single hook that seeds the test API key inside uvicorn's event loop.
app.router.on_startup = []
app.router.on_shutdown = []


@app.on_event("startup")
async def _seed_auth():
    key_hash = hashlib.sha256(T.API_KEY.encode("utf-8")).hexdigest()
    await auth_redis.set(f"auth:apikey:{key_hash}", json.dumps(T._auth_payload()))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8399, log_level="warning")

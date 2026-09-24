"""P3c: identity cache fill-after-invalidate race, REAL rvproto Identity + KillSwitch + Admission.

The request-path HGET for an uncached key is answered by the store BEFORE the key is revoked, but
its reply reaches the worker AFTER the background refresher has seen the new auth_epoch and cleared
the cache (a delayed segment on that one connection). Identity.fetch then inserts the pre-revocation
principal into the freshly cleared cache, where no later epoch read removes it.
The fault proxy (26380) delays exactly one reply: the one to `HGET rv:keys ...`.
  rvproto/.venv/bin/python p3_identity_race.py <reply_delay_ms>
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import urllib.request

sys.path.insert(0, __import__("os").environ.get("RVPROTO_DIR", "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-state-propagation/rvproto-frozen1"))

import redis
import redis.asyncio as aioredis

from rvproto.admit.admission import Admission, Grant
from rvproto.admit.identity import Identity
from rvproto.admit.killswitch import KillSwitch
from rvproto.admit.quota import Gcra, TokenLease
from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import K_AUTH_EPOCH, K_BUDGET_PREFIX, K_KEYS, K_KILLSWITCH

DIRECT = "redis://127.0.0.1:26379/6"
PROXIED = "redis://127.0.0.1:26380/6"
CTL = "http://127.0.0.1:26390"
KEY = "sk-rv-revoke-me-0001"
H = hashlib.sha256(KEY.encode()).hexdigest()


def ctl(path: str) -> dict:
    return json.loads(urllib.request.urlopen(CTL + path, timeout=5).read())


def log(step: str, **kw: object) -> None:
    print(json.dumps({"t": round(time.time(), 3), "step": step, **kw}), flush=True)


def verdict(g: object) -> str:
    return "ADMITTED (Grant)" if isinstance(g, Grant) else f"REJECTED {g.status} {g.code}"  # type: ignore[attr-defined]


async def main() -> None:
    delay_ms = float(sys.argv[1])
    r = redis.Redis.from_url(DIRECT)
    r.flushdb()
    r.hset(K_KEYS, H, json.dumps({"key_id": "k-revoke", "org_id": "org-a", "rate_per_s": 1e6, "burst": 1e6, "epoch": 1}))
    r.set(K_AUTH_EPOCH, 1)
    r.set(K_KILLSWITCH, "0")
    r.set(K_BUDGET_PREFIX + "org-a", 10**12)
    metrics = Registry(0)
    req_pool = aioredis.Redis.from_url(PROXIED, socket_timeout=delay_ms / 1000 + 1)  # request path
    bg_pool = aioredis.Redis.from_url(DIRECT)  # background refresher (its own connections)
    ident = Identity(req_pool)
    ks = KillSwitch(bg_pool, metrics, refresh_ms=500, stale_ms=5000, on_epoch=ident.on_epoch)
    adm = Admission(ident, ks, Gcra(), TokenLease(req_pool, metrics, chunk_tokens=10_000))
    await ks.refresh_once()
    ctl("/delay_reply_off")
    ctl(f"/delay_reply?match=HGET&match2=rv:keys&ms={delay_ms}&count=1")
    log("0 key valid, epoch=1, cache empty; request arrives (cache miss -> HGET)")
    req = asyncio.create_task(adm.admit(KEY, 100, "gpt-4o-mini"))
    await asyncio.sleep(min(0.05, delay_ms / 4000))
    p = r.pipeline(transaction=True)  # revocation, done right: delete THEN bump the epoch, atomically
    p.hdel(K_KEYS, H)
    p.incr(K_AUTH_EPOCH)
    p.execute()
    log("1 key REVOKED in the store (HDEL + INCR auth_epoch in one MULTI)", store_has_key=bool(r.hexists(K_KEYS, H)),
        store_epoch=int(r.get(K_AUTH_EPOCH)))
    await ks.refresh_once()
    log("2 background refresher saw epoch 2 -> cache cleared", identity_epoch=ident.epoch,
        cache_size=len(ident._cache))
    g = await req
    log("3 the in-flight request completes", verdict=verdict(g), cache_now_holds_revoked_key=H in ident._cache)
    for i in range(1, 7):
        await asyncio.sleep(1.0)
        await ks.refresh_once()  # the real refresher keeps running every 500 ms
        g2 = await adm.admit(KEY, 100, "gpt-4o-mini")
        log(f"4 +{i}s later: a NEW request with the revoked key", verdict=verdict(g2),
            store_has_key=bool(r.hexists(K_KEYS, H)), identity_epoch=ident.epoch)
    print(json.dumps({"proxy": ctl("/stats")}))


if __name__ == "__main__":
    asyncio.run(main())

"""Key -> Principal: per-process RAM cache; shared store (HGET) on miss only.

Revocation is epoch-based: the shared-state refresher reads rv:auth_epoch in the
background; an epoch change clears the cache, so revocation is bounded by the
refresh period without a per-request read.
"""

from __future__ import annotations

import hashlib

import orjson
import redis.asyncio as aioredis

from rvproto.domain.request import Principal
from rvproto.runtime.store import K_KEYS


class Identity:
    def __init__(self, r: aioredis.Redis) -> None:
        self.r = r
        self._cache: dict[str, Principal] = {}
        self.epoch: int | None = None

    def on_epoch(self, epoch: int) -> None:
        if self.epoch is not None and epoch != self.epoch:
            self._cache = {}
        self.epoch = epoch

    def cached(self, api_key: str) -> tuple[str, Principal | None]:
        h = hashlib.sha256(api_key.encode()).hexdigest()
        return h, self._cache.get(h)

    async def fetch(self, key_hash: str) -> Principal | None:
        """One shared-state round trip. Raises StoreError on outage."""
        raw = await self.r.hget(K_KEYS, key_hash)
        if raw is None:
            return None
        d = orjson.loads(raw)
        p = Principal(
            key_id=str(d["key_id"]),
            org_id=str(d["org_id"]),
            rate_per_s=float(d["rate_per_s"]),
            burst=float(d["burst"]),
            epoch=int(d.get("epoch", 0)),
        )
        self._cache[key_hash] = p
        return p

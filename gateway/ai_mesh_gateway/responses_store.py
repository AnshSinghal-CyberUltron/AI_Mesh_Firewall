"""Tenant-isolated store for the OpenAI Responses API (``store=true`` +
``previous_response_id`` chaining, ``GET``/``DELETE``/``input_items``).

Isolation model (R3 in the master plan): every key is namespaced by ``org_id``
(``responses:{org_id}:{response_id}``), so org A can NEVER read or continue org
B's response — a lookup for another org's id resolves to a different key that
does not exist. The stored ``org_id`` is re-asserted on read as defense in depth.
Only REDACTED output is persisted (the firewall already redacted it before the
client saw it), and the policy hash at write time is recorded so replay can be
re-scanned against current policy (R4).
"""
from __future__ import annotations

import json
import time
from typing import Any

_PREFIX = "responses"
_DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def _key(org_id: Any, response_id: str) -> str:
    return f"{_PREFIX}:{org_id}:{response_id}"


class ResponseStore:
    """Async wrapper over the gateway Redis client. All methods are org-scoped;
    callers MUST pass the authenticated ``org_id`` (never a client-supplied one)."""

    def __init__(self, redis_client, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
        self._redis = redis_client
        self._ttl = ttl_seconds

    @property
    def enabled(self) -> bool:
        return self._redis is not None

    async def save(self, org_id: Any, response_obj: dict, *,
                   replay_messages: list[dict], input_items: list | None = None,
                   policies_hash: str | None = None) -> None:
        """Persist a completed response under the org namespace. ``response_obj`` is
        the client-facing (already-redacted) Responses object."""
        if self._redis is None or not isinstance(response_obj, dict):
            return
        rid = response_obj.get("id")
        if not rid:
            return
        record = {
            "org_id": org_id,
            "response": response_obj,
            "replay_messages": replay_messages or [],
            "input_items": input_items or [],
            "policies_hash": policies_hash,
            "created_at": int(time.time()),
        }
        try:
            await self._redis.set(_key(org_id, rid), json.dumps(record), ex=self._ttl)
        except Exception:
            # Non-fatal: store is best-effort; inference already succeeded.
            return

    async def _load_record(self, org_id: Any, response_id: str) -> dict | None:
        if self._redis is None or not response_id:
            return None
        try:
            raw = await self._redis.get(_key(org_id, response_id))
        except Exception:
            return None
        if not raw:
            return None
        try:
            rec = json.loads(raw)
        except (TypeError, ValueError):
            return None
        # defense in depth: the stored org must match the requesting org
        if str(rec.get("org_id")) != str(org_id):
            return None
        return rec

    async def get_response(self, org_id: Any, response_id: str) -> dict | None:
        rec = await self._load_record(org_id, response_id)
        return rec.get("response") if rec else None

    async def get_replay_messages(self, org_id: Any, response_id: str) -> list[dict] | None:
        """Chat messages to prepend for ``previous_response_id`` continuation.
        Returns None when the parent is unknown/cross-org (caller fails closed)."""
        rec = await self._load_record(org_id, response_id)
        if rec is None:
            return None
        prior = rec.get("replay_messages") or []
        # include the parent's own input items (as messages) so the chain has the
        # full prior turn, then the parent's assistant output.
        return prior

    async def get_input_items(self, org_id: Any, response_id: str) -> list | None:
        rec = await self._load_record(org_id, response_id)
        return (rec.get("input_items") or []) if rec else None

    async def delete(self, org_id: Any, response_id: str) -> bool:
        if self._redis is None or not response_id:
            return False
        # only delete if it belongs to this org
        rec = await self._load_record(org_id, response_id)
        if rec is None:
            return False
        try:
            await self._redis.delete(_key(org_id, response_id))
            return True
        except Exception:
            return False

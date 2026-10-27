"""Org-scoped store for ``/v1/responses`` (GW15 close-out, task 5.1).

A stored record is the client-facing Responses object, the replay messages a
``previous_response_id`` continues from, and the input items. Every record is
keyed by the caller's org. A lookup with another org's id misses, and the stored
org is checked again on read.

A write that fails returns ``False``. The caller must not report the id as
stored. v1 swallowed that failure and still returned HTTP 200.

Only single-key ``SET``, ``GET``, and ``DEL`` are used, so no request does work
proportional to the number of tenants.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from gateway_v2.runtime.store_keys import KEYS, StoreKeys

__all__ = (
    "MemoryResponseStore",
    "ResponseStore",
    "StoredResponse",
    "ValkeyResponseStore",
)

_ID_PREFIX = "resp_"


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """One stored response. Holds enforced text only, and no detector evidence."""

    response: Mapping[str, object]
    replay_messages: tuple[Mapping[str, object], ...] = ()
    input_items: tuple[Mapping[str, object], ...] = ()

    @property
    def response_id(self) -> str:
        value = self.response.get("id")
        return value if isinstance(value, str) else ""


class ResponseStore(Protocol):
    """The three operations the Responses route needs. Every call names the caller's org."""

    async def put(self, org_id: str, record: StoredResponse) -> bool: ...

    async def get(self, org_id: str, response_id: str) -> StoredResponse | None: ...

    async def delete(self, org_id: str, response_id: str) -> bool: ...


def _storable(org_id: str, record: StoredResponse) -> bool:
    return bool(org_id) and record.response_id.startswith(_ID_PREFIX)


class MemoryResponseStore:
    """In-process store keyed by ``(org_id, response_id)``. For tests and single-worker runs."""

    __slots__ = ("_records",)

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], StoredResponse] = {}

    async def put(self, org_id: str, record: StoredResponse) -> bool:
        if not _storable(org_id, record):
            return False
        self._records[(org_id, record.response_id)] = record
        return True

    async def get(self, org_id: str, response_id: str) -> StoredResponse | None:
        if not org_id or not response_id:
            return None
        return self._records.get((org_id, response_id))

    async def delete(self, org_id: str, response_id: str) -> bool:
        if not org_id or not response_id:
            return False
        return self._records.pop((org_id, response_id), None) is not None


class ValkeyResponseStore:
    """Valkey or Redis store. ``client`` is an injected ``redis.asyncio`` client.

    Key ``{rv2}:responses:{org}:{id}``, TTL from the resource contract. A client error
    makes ``put`` return ``False`` and ``get`` return ``None``. This class never touches
    any other key family.
    """

    __slots__ = ("_client", "_keys", "_ttl_s")

    def __init__(self, client: Any, *, ttl_s: int, keys: StoreKeys = KEYS) -> None:
        if ttl_s < 1:
            raise ValueError("a response store TTL must be at least one second")
        self._client = client
        self._ttl_s = ttl_s
        self._keys = keys

    async def put(self, org_id: str, record: StoredResponse) -> bool:
        if not _storable(org_id, record):
            return False
        body = json.dumps(
            {
                "org_id": org_id,
                "response": dict(record.response),
                "replay_messages": [dict(item) for item in record.replay_messages],
                "input_items": [dict(item) for item in record.input_items],
            },
            separators=(",", ":"),
        )
        try:
            result = await self._client.set(
                self._keys.response(org_id, record.response_id), body, ex=self._ttl_s
            )
        except Exception:
            return False
        return bool(result)

    async def get(self, org_id: str, response_id: str) -> StoredResponse | None:
        if not org_id or not response_id:
            return None
        try:
            raw = await self._client.get(self._keys.response(org_id, response_id))
        except Exception:
            return None
        return _decode(raw, org_id)

    async def delete(self, org_id: str, response_id: str) -> bool:
        if not org_id or not response_id:
            return False
        try:
            removed = await self._client.delete(self._keys.response(org_id, response_id))
        except Exception:
            return False
        return isinstance(removed, int) and removed > 0


def _decode(raw: object, org_id: str) -> StoredResponse | None:
    """Parse one stored record. A record whose stored org differs is treated as missing."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(decoded, dict) or decoded.get("org_id") != org_id:
        return None
    response = decoded.get("response")
    if not isinstance(response, dict):
        return None
    return StoredResponse(
        response=response,
        replay_messages=_mappings(decoded.get("replay_messages")),
        input_items=_mappings(decoded.get("input_items")),
    )


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))

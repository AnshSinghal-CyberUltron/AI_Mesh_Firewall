"""GW31 ChunkStore: the model-visible text behind each indexed vector.

GW30 transports persist and return only ids and vectors, never ``VectorRecord.metadata``, so
chunk text, source and trust cannot come from the adapter. GW31 owns this store, keyed by
``(wire_collection, record_id)``. The wire name embeds org and project, so a cross-tenant read is
structurally impossible. The in-memory store REFUSES a put when full rather than evicting.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain.rag import TrustLevel

__all__ = ("ChunkEntry", "ChunkStore", "InMemoryChunkStore")


@dataclass(frozen=True, slots=True)
class ChunkEntry:
    text: str
    vector: tuple[float, ...]
    source: str
    trust: TrustLevel


class ChunkStore(Protocol):
    async def put_many(self, collection: str, entries: Mapping[str, ChunkEntry]) -> bool: ...

    async def get_many(
        self, collection: str, ids: tuple[str, ...]
    ) -> Mapping[str, ChunkEntry] | None: ...

    async def delete_many(self, collection: str, ids: tuple[str, ...]) -> bool: ...


def _size(entry: ChunkEntry, vector_bytes_per_dim: int) -> int:
    return len(entry.text.encode()) + len(entry.vector) * vector_bytes_per_dim


class InMemoryChunkStore:
    """Bounded by entry count and bytes. Idempotent overwrite charges per distinct key."""

    def __init__(self, max_entries: int, max_bytes: int, vector_bytes_per_dim: int) -> None:
        if max_entries < 1 or max_bytes < 1 or vector_bytes_per_dim < 1:
            raise ValueError("chunk store bounds must be positive")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._per_dim = vector_bytes_per_dim
        self._data: dict[tuple[str, str], ChunkEntry] = {}
        self._bytes = 0

    def __len__(self) -> int:
        return len(self._data)

    async def put_many(self, collection: str, entries: Mapping[str, ChunkEntry]) -> bool:
        new_keys = 0
        delta = 0
        for rid, entry in entries.items():
            old = self._data.get((collection, rid))
            if old is None:
                new_keys += 1
                delta += _size(entry, self._per_dim)
            else:
                delta += _size(entry, self._per_dim) - _size(old, self._per_dim)
        if len(self._data) + new_keys > self._max_entries or self._bytes + delta > self._max_bytes:
            return False
        for rid, entry in entries.items():
            self._data[(collection, rid)] = entry
        self._bytes += delta
        return True

    async def get_many(
        self, collection: str, ids: tuple[str, ...]
    ) -> Mapping[str, ChunkEntry] | None:
        return {
            rid: self._data[(collection, rid)] for rid in ids if (collection, rid) in self._data
        }

    async def delete_many(self, collection: str, ids: tuple[str, ...]) -> bool:
        for rid in ids:
            old = self._data.pop((collection, rid), None)
            if old is not None:
                self._bytes -= _size(old, self._per_dim)
        return True

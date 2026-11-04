"""GW31 ChunkStore and embedder."""
from __future__ import annotations

import asyncio

from gateway_v2.dispatch.rag_chunks import ChunkEntry, InMemoryChunkStore
from gateway_v2.dispatch.rag_embedding import DeterministicHashEmbedder
from gateway_v2.dispatch.rag_ranker import relevance_of
from gateway_v2.domain.rag import TrustLevel


def _e(text: str = "hello") -> ChunkEntry:
    return ChunkEntry(text, (1.0, 0.0), "s", TrustLevel.MEDIUM)


def test_put_get_delete_and_scoping() -> None:
    async def go() -> None:
        s = InMemoryChunkStore(10, 10_000, 8)
        assert await s.put_many("c1", {"a": _e()})
        assert await s.get_many("c1", ("a", "b")) == {"a": _e()}
        assert await s.get_many("c2", ("a",)) == {}
        assert await s.delete_many("c1", ("a",))
        assert len(s) == 0

    asyncio.run(go())


def test_refuses_when_full_and_overwrite_is_free() -> None:
    async def go() -> None:
        s = InMemoryChunkStore(2, 10_000, 8)
        assert await s.put_many("c", {"a": _e(), "b": _e()})
        assert not await s.put_many("c", {"z": _e()})
        assert await s.put_many("c", {"a": _e("changed")})
        assert len(s) == 2

    asyncio.run(go())


def test_byte_bound() -> None:
    async def go() -> None:
        s = InMemoryChunkStore(100, 20, 8)
        assert not await s.put_many("c", {"a": _e("x" * 50)})

    asyncio.run(go())


def test_embedder_is_deterministic_and_related_text_scores_higher() -> None:
    async def go() -> None:
        emb = DeterministicHashEmbedder(64)
        out = await emb.embed("o", ("the cat sat", "the cat sat", "quantum chromodynamics"))
        assert out is not None
        assert out[0] == out[1]
        same = relevance_of(out[0], out[1])
        other = relevance_of(out[0], out[2])
        assert same is not None and other is not None and same > other

    asyncio.run(go())

"""E11 (b) upsert-time embedding-validity guard.

rag_ingest embeds documents and upserts the resulting vectors with no validity
check, so a corrupt/adversarial embedding (NaN/inf, all-zero, or wildly-scaled
magnitude) would be written and later surface as a "real" nearest neighbour.
The vector-write choke point now rejects such vectors at upsert time.

These tests prove the guard:
  * REJECTS NaN / inf / all-zero / abnormal-magnitude vectors,
  * ACCEPTS a normal unit-ish vector (no false positive),
  * drops a rejected vector's PAIRED id/document/metadata in lockstep so the
    surviving batch stays index-aligned,
  * is wired into PineconeClient._upsert_sync (only valid vectors are written).
"""
import math
import os
import sys
from unittest.mock import MagicMock

# Make the bare gateway modules importable regardless of cwd / PYTHONPATH so the
# exact task command (`cd gateway && .venv/bin/python -m pytest ...`) works.
_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

import pytest

from vector_client import (
    _embedding_validity_reason,
    filter_valid_embedding_batch,
    PineconeClient,
)


# ─────────────────────────── per-vector validity ───────────────────────────


def _unit_ish_vector(dim: int = 8, scale: float = 1.0) -> list[float]:
    """A normal embedding: nonzero, finite, with a sane L2 norm."""
    raw = [0.3, -0.2, 0.5, 0.1, -0.4, 0.25, 0.15, -0.35][:dim]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [scale * x / norm for x in raw]


def test_accepts_normal_unit_vector_no_false_positive():
    assert _embedding_validity_reason(_unit_ish_vector()) is None


def test_accepts_unnormalized_but_sane_magnitude():
    # Many models emit unnormalized vectors with norms of a few tens.
    assert _embedding_validity_reason(_unit_ish_vector(scale=42.0)) is None


def test_rejects_nan_component():
    v = _unit_ish_vector()
    v[3] = float("nan")
    assert _embedding_validity_reason(v) == "NaN component"


def test_rejects_positive_inf_component():
    v = _unit_ish_vector()
    v[0] = float("inf")
    assert _embedding_validity_reason(v) == "inf component"


def test_rejects_negative_inf_component():
    v = _unit_ish_vector()
    v[0] = float("-inf")
    assert _embedding_validity_reason(v) == "inf component"


def test_rejects_all_zeros():
    assert _embedding_validity_reason([0.0] * 8) is not None


def test_rejects_near_zero_norm():
    assert _embedding_validity_reason([1e-9] * 8) is not None


def test_rejects_empty_vector():
    assert _embedding_validity_reason([]) is not None


def test_rejects_abnormal_huge_magnitude():
    # A wildly-scaled (poisoned) vector well outside the sane band.
    assert _embedding_validity_reason([1e6] * 8) is not None


def test_rejects_non_numeric_component():
    v = _unit_ish_vector()
    v[2] = "not-a-number"
    assert _embedding_validity_reason(v) is not None


# ──────────────────── batch filtering stays index-aligned ────────────────────


def test_batch_drops_poisoned_in_lockstep():
    docs = ["doc-a", "doc-b", "doc-c", "doc-d"]
    ids = ["id-a", "id-b", "id-c", "id-d"]
    metas = [{"k": "a"}, {"k": "b"}, {"k": "c"}, {"k": "d"}]
    embeddings = [
        _unit_ish_vector(),          # a — valid
        [float("nan")] * 8,          # b — poisoned
        [0.0] * 8,                   # c — all-zero
        _unit_ish_vector(scale=10),  # d — valid
    ]

    f_docs, f_ids, f_emb, f_meta, rejected = filter_valid_embedding_batch(
        docs, ids, embeddings, metas
    )

    assert rejected == 2
    # Only the two valid rows survive, and id/doc/metadata stay paired.
    assert f_ids == ["id-a", "id-d"]
    assert f_docs == ["doc-a", "doc-d"]
    assert f_meta == [{"k": "a"}, {"k": "d"}]
    assert len(f_emb) == 2
    # All four output lists are the same length (index-aligned).
    assert len({len(f_docs), len(f_ids), len(f_emb), len(f_meta)}) == 1


def test_batch_all_valid_passes_through_unchanged():
    docs = ["d0", "d1"]
    ids = ["i0", "i1"]
    metas = [{"x": 0}, {"x": 1}]
    embeddings = [_unit_ish_vector(), _unit_ish_vector(scale=5)]
    f_docs, f_ids, f_emb, f_meta, rejected = filter_valid_embedding_batch(
        docs, ids, embeddings, metas
    )
    assert rejected == 0
    assert (f_docs, f_ids, f_meta) == (docs, ids, metas)
    assert len(f_emb) == 2


def test_batch_handles_none_metadata():
    docs = ["d0", "d1"]
    ids = ["i0", "i1"]
    embeddings = [[float("inf")] * 8, _unit_ish_vector()]
    f_docs, f_ids, f_emb, f_meta, rejected = filter_valid_embedding_batch(
        docs, ids, embeddings, None
    )
    assert rejected == 1
    assert f_ids == ["i1"]
    assert f_docs == ["d1"]
    assert f_meta is None
    assert len(f_emb) == 1


# ─────────────── guard is wired into the Pinecone write path ───────────────


def _pinecone_with_fake_index():
    """A PineconeClient whose _embed + index are stubbed so we can assert what
    actually reaches index.upsert() without touching a real Pinecone."""
    client = PineconeClient(api_key="fake-key", embedding_model="test-embed")

    fake_index = MagicMock()
    fake_pc = MagicMock()
    fake_pc.Index.return_value = fake_index
    client._pc = fake_pc  # bypass lazy real-client init
    return client, fake_index


def test_upsert_drops_poisoned_vectors_before_write():
    client, fake_index = _pinecone_with_fake_index()

    docs = ["good-1", "poison", "good-2"]
    ids = ["id1", "id2", "id3"]
    metas = [{"k": 1}, {"k": 2}, {"k": 3}]
    # Stub embedding: middle doc gets an all-zero (poisoned) vector.
    client._embed = MagicMock(return_value=[
        _unit_ish_vector(),
        [0.0] * 8,
        _unit_ish_vector(scale=3),
    ])

    written = client._upsert_sync("coll", docs, ids, metas, project_id="proj1")

    # Only the 2 legitimate vectors were written.
    assert written == 2
    assert fake_index.upsert.call_count == 1
    sent_vectors = fake_index.upsert.call_args.kwargs["vectors"]
    sent_ids = [v["id"] for v in sent_vectors]
    assert sent_ids == ["id1", "id3"]
    # The poisoned id never reached the store.
    assert "id2" not in sent_ids
    # Metadata stayed paired with the surviving ids.
    assert sent_vectors[0]["metadata"]["k"] == 1
    assert sent_vectors[1]["metadata"]["k"] == 3
    assert sent_vectors[0]["metadata"]["content"] == "good-1"
    assert sent_vectors[1]["metadata"]["content"] == "good-2"


def test_upsert_writes_nothing_when_entire_batch_poisoned():
    client, fake_index = _pinecone_with_fake_index()
    docs = ["p1", "p2"]
    ids = ["id1", "id2"]
    client._embed = MagicMock(return_value=[[float("nan")] * 8, [0.0] * 8])

    written = client._upsert_sync("coll", docs, ids, None, project_id="proj1")

    assert written == 0
    fake_index.upsert.assert_not_called()


def test_upsert_all_valid_writes_full_batch():
    client, fake_index = _pinecone_with_fake_index()
    docs = ["a", "b"]
    ids = ["id1", "id2"]
    client._embed = MagicMock(return_value=[_unit_ish_vector(), _unit_ish_vector(scale=2)])

    written = client._upsert_sync("coll", docs, ids, None, project_id="proj1")

    assert written == 2
    sent_ids = [v["id"] for v in fake_index.upsert.call_args.kwargs["vectors"]]
    assert sent_ids == ["id1", "id2"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))

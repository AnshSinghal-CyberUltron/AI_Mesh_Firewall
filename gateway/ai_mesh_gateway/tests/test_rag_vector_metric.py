"""RAG-05b: Chroma distance-metric normalization.

The policy contract defines ``anomaly_distance_threshold`` as a COSINE distance
in [0,1] (control-plane FloatField, default 0.85, MaxValue 1.0 — an operator
cannot compensate). Pinecone honors that unit; Chroma did not. A collection
created without ``metadata={"hnsw:space": ...}`` uses Chroma's default ``l2``
space, whose distance is SQUARED L2 — exactly 2x the cosine distance for
unit-normalized vectors. Passing it through verbatim flagged EVERY document in a
legitimate result set, which ranker_stage turns into a 100% block.

These tests mock the Chroma client entirely (no live Chroma required). The
conversion factors they assert were measured against chromadb 1.5.9, where a
query/doc pair 60deg apart returns 1.0 in ``l2`` space and 0.5 in ``cosine``.
"""
from __future__ import annotations

import pytest

from vector_client import (
    ChromaDBClient,
    _chroma_collection_space,
    _chroma_cosine_distances,
)


class _FakeCollection:
    """Stand-in for a chromadb Collection.

    ``configuration`` mirrors chromadb >= 1.x, which materializes the EFFECTIVE
    space even when the caller never set one. ``metadata`` mirrors the legacy
    ``{"hnsw:space": ...}`` surface (and is ``None`` for a default collection).
    """

    def __init__(self, distances, *, configuration=None, metadata=None, ids=None):
        self.configuration = configuration
        self.metadata = metadata
        self._distances = list(distances)
        self._ids = list(ids) if ids is not None else [
            f"d{i}" for i in range(len(self._distances))
        ]
        self.last_query_params = None
        self.added = []

    def add(self, **kwargs):
        self.added.append(kwargs)

    def query(self, **params):
        self.last_query_params = params
        return {
            "ids": [self._ids],
            "documents": [[f"content-{i}" for i in self._ids]],
            "metadatas": [[{} for _ in self._ids]],
            "distances": [self._distances],
        }


class _FakeChromaClient:
    def __init__(self, collection):
        self._collection = collection
        self.created = []

    def get_collection(self, name):
        return self._collection

    def get_or_create_collection(self, name, metadata=None):
        self.created.append({"name": name, "metadata": metadata})
        return self._collection


def _client_with(collection) -> ChromaDBClient:
    client = ChromaDBClient(url="http://chroma.internal:8000")
    client._client = _FakeChromaClient(collection)
    return client


def _query(collection) -> list[dict]:
    return _client_with(collection)._query_sync(
        collection_name="docs",
        query_text="q",
        n_results=10,
        where=None,
        project_id="org7-proj",
    )


def _hnsw(space):
    return {"hnsw": {"space": space, "ef_construction": 100}, "spann": None}


# ── The headline break: an l2 collection is 2x the cosine scale ──


def test_l2_collection_squared_distances_are_halved_to_cosine():
    """[0.9, 1.1, 1.3] squared-L2 -> [0.45, 0.55, 0.65] cosine.

    Every one of these is above the 0.85 default threshold on the raw scale and
    below it after conversion, which is the whole availability break.
    """
    docs = _query(_FakeCollection([0.9, 1.1, 1.3], configuration=_hnsw("l2")))

    assert [d["distance"] for d in docs] == pytest.approx([0.45, 0.55, 0.65])
    assert all(d["_distance_metric"] == "cosine" for d in docs)
    # Proves the fix undoes the block: nothing exceeds the policy default.
    assert all(d["distance"] <= 0.85 for d in docs)


def test_l2_conversion_defeats_the_block_the_raw_scale_caused():
    """detect_embedding_anomaly's absolute path flagged all 3 docs; now none."""
    from context_guard import ContextGuard

    raw = [0.9, 1.1, 1.3, 1.5, 1.7]
    threshold = 0.85

    guard = ContextGuard.__new__(ContextGuard)  # detect_* needs no wiring
    assert guard.detect_embedding_anomaly(raw, threshold) == [0, 1, 2, 3, 4]

    converted = [d["distance"] for d in _query(
        _FakeCollection(raw, configuration=_hnsw("l2"))
    )]
    assert guard.detect_embedding_anomaly(converted, threshold) == []


# ── cosine passes through; ip is already 1 - IP (NOT the bare inner product) ──


def test_cosine_collection_passes_through_unchanged():
    raw = [0.12, 0.44, 0.91]
    docs = _query(_FakeCollection(raw, configuration=_hnsw("cosine")))

    assert [d["distance"] for d in docs] == pytest.approx(raw)
    assert all(d["_distance_metric"] == "cosine" for d in docs)


def test_ip_collection_passes_through_unchanged():
    """hnswlib's ``ip`` distance is ``1 - inner_product``, which already equals
    the cosine distance for unit-normalized vectors. Applying ``1 - value``
    would INVERT the metric (a perfect match would read as maximally anomalous).
    """
    raw = [0.0, 0.5, 1.0]
    docs = _query(_FakeCollection(raw, configuration=_hnsw("ip")))

    assert [d["distance"] for d in docs] == pytest.approx(raw)
    assert all(d["_distance_metric"] == "cosine" for d in docs)


def test_legacy_metadata_hnsw_space_is_honored():
    """Older collections expose the space only via ``metadata["hnsw:space"]``."""
    docs = _query(_FakeCollection([1.0, 2.0], metadata={"hnsw:space": "l2"}))

    assert [d["distance"] for d in docs] == pytest.approx([0.5, 1.0])
    assert all(d["_distance_metric"] == "cosine" for d in docs)


# ── Fail-safe: never guess, never newly block ──


def test_absent_space_passes_through_raw_and_is_marked_unknown():
    """A default collection reports ``metadata is None``. If the space cannot be
    determined at all we must NOT guess a conversion — emit the raw value and
    say so, so downstream falls back to the scale-invariant 2-sigma path.
    """
    raw = [0.9, 1.1, 1.3]
    docs = _query(_FakeCollection(raw, configuration=None, metadata=None))

    assert [d["distance"] for d in docs] == pytest.approx(raw)
    assert all(d["_distance_metric"] == "unknown" for d in docs)
    # No score is invented for an uncalibrated scale — the retriever's relevance
    # filter prefers `score` over `distance`, so a guessed one could newly drop.
    assert all("score" not in d for d in docs)


def test_unrecognized_space_is_marked_unknown():
    docs = _query(_FakeCollection([0.9, 1.1], configuration=_hnsw("hamming")))

    assert [d["distance"] for d in docs] == pytest.approx([0.9, 1.1])
    assert all(d["_distance_metric"] == "unknown" for d in docs)


def test_unnormalized_l2_corpus_is_not_converted():
    """Squared L2 between UNIT vectors cannot exceed 4. A set that breaches the
    bound proves the corpus is not unit-normalized, so ``/2`` does not apply —
    pass through raw rather than emit a wrong number.
    """
    raw = [12.5, 40.0, 96.25]
    docs = _query(_FakeCollection(raw, configuration=_hnsw("l2")))

    assert [d["distance"] for d in docs] == pytest.approx(raw)
    assert all(d["_distance_metric"] == "unknown" for d in docs)


def test_unnormalized_ip_corpus_is_not_converted():
    """``1 - inner_product`` goes negative once magnitudes exceed unit length."""
    raw = [-3.5, 0.5]
    docs = _query(_FakeCollection(raw, configuration=_hnsw("ip")))

    assert [d["distance"] for d in docs] == pytest.approx(raw)
    assert all(d["_distance_metric"] == "unknown" for d in docs)


def test_non_numeric_distance_keeps_the_whole_set_raw():
    """One bad value must not leave the set in mixed units."""
    values, metric = _chroma_cosine_distances([0.9, None, 1.3], "l2")

    assert values == [0.9, None, 1.3]
    assert metric == "unknown"


def test_clamp_holds_for_out_of_range_l2_input():
    """At the very top of the unit-normalized band (4.0) the halved value is 2.0
    — the true maximum of a cosine distance — and cannot exceed it.
    """
    values, metric = _chroma_cosine_distances([4.0, 0.0, -1e-9], "l2")

    assert metric == "cosine"
    assert values == pytest.approx([2.0, 0.0, 0.0])
    assert all(0.0 <= v <= 2.0 for v in values)


def test_conversion_never_flags_more_documents_than_before():
    """The core safety property: a conversion only ever SHRINKS a distance, so
    it can never push a document over a threshold that it previously cleared.
    """
    for space, raw in (
        ("l2", [0.0, 0.3, 1.7, 3.9]),
        ("cosine", [0.0, 0.3, 1.7, 1.99]),
        ("ip", [0.0, 0.3, 1.7, 1.99]),
        ("hamming", [0.0, 0.3, 1.7, 9.5]),
        (None, [0.0, 0.3, 1.7, 9.5]),
    ):
        converted, _metric = _chroma_cosine_distances(raw, space)
        assert all(
            float(c) <= float(r) for c, r in zip(converted, raw)
        ), f"space={space} produced a LARGER distance"


# ── Emitted similarity ──


def test_score_is_emitted_for_a_known_metric_and_is_never_more_restrictive():
    """The retriever's relevance filter prefers ``score`` over ``distance``, and
    derives ``max(0, min(1, 1 - distance))`` when only ``distance`` is present.
    The emitted score must never sit BELOW that, or the filter would newly drop.
    """
    raw = [0.9, 1.1, 1.3]
    docs = _query(_FakeCollection(raw, configuration=_hnsw("l2")))

    assert [d["score"] for d in docs] == pytest.approx([0.55, 0.45, 0.35])
    for doc, raw_distance in zip(docs, raw):
        derived_before = max(0.0, min(1.0, 1.0 - raw_distance))
        assert doc["score"] >= derived_before


def test_score_stays_within_zero_and_one_for_an_anticorrelated_cosine_doc():
    """A cosine distance legitimately reaches 2.0, which would make ``1 - d``
    negative; the relevance filter is defined on a [0,1] similarity.
    """
    docs = _query(_FakeCollection([1.6], configuration=_hnsw("cosine")))

    assert docs[0]["distance"] == pytest.approx(1.6)  # distance NOT clamped
    assert docs[0]["score"] == pytest.approx(0.0)


# ── Space introspection ──


def test_space_is_read_from_configuration_before_metadata():
    collection = _FakeCollection(
        [], configuration=_hnsw("cosine"), metadata={"hnsw:space": "l2"},
    )
    assert _chroma_collection_space(collection) == "cosine"


def test_space_introspection_survives_a_raising_property():
    class _Hostile:
        @property
        def configuration(self):
            raise RuntimeError("unsupported by this server version")

        @property
        def configuration_json(self):
            raise RuntimeError("unsupported by this server version")

        metadata = {"hnsw:space": "L2 "}

    # Falls through to the legacy surface, normalized for case/whitespace.
    assert _chroma_collection_space(_Hostile()) == "l2"


def test_space_introspection_returns_none_when_nothing_is_exposed():
    class _Opaque:
        pass

    assert _chroma_collection_space(_Opaque()) is None


# ── Collection creation pins the cosine space ──


def test_new_collections_are_created_in_cosine_space():
    collection = _FakeCollection([])
    client = _client_with(collection)

    client._create_collection_sync("docs", "org7-proj")
    client._add_sync("docs", ["a"], ["id-a"], None, "org7-proj")

    assert [c["metadata"] for c in client._client.created] == [
        {"hnsw:space": "cosine"},
        {"hnsw:space": "cosine"},
    ]
    assert all(c["name"] == "org7-proj__docs" for c in client._client.created)


# ── Pinecone keeps its semantics and gains the marker ──


def test_pinecone_documents_declare_the_cosine_metric():
    from vector_client import PineconeClient

    class _Index:
        def query(self, **_kwargs):
            return {"matches": [{"id": "p1", "score": 0.8, "metadata": {"content": "c"}}]}

    class _PC:
        def Index(self, _name):
            return _Index()

    client = PineconeClient(api_key="k")
    client._pc = _PC()
    client._embed = lambda texts, input_type: [[0.1, 0.2]]

    docs = client._query_sync("idx", "q", 5, None, "org7-proj")

    assert docs[0]["_distance_metric"] == "cosine"
    # Unchanged semantics.
    assert docs[0]["distance"] == pytest.approx(0.2)
    assert docs[0]["score"] == pytest.approx(0.8)


# ── The marker must not reach the client ──


def test_distance_metric_is_stripped_before_client_egress():
    from main import _strip_internal_doc_fields

    docs = _query(_FakeCollection([1.0], configuration=_hnsw("l2")))
    _strip_internal_doc_fields(docs)

    assert "_distance_metric" not in docs[0]
    assert {"id", "content", "metadata", "distance", "score"} <= set(docs[0])

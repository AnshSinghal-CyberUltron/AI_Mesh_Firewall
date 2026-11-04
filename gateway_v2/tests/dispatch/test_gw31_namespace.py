"""GW31 namespace encoding (D-3)."""
from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from gateway_v2.dispatch.rag_namespace import (
    NamespaceRefused,
    build_namespace,
    encode_segment,
    parse_wire,
    validate_segment,
    wire_collection,
)
from gateway_v2.dispatch.vector_transports._common import safe_name
from gateway_v2.runtime.resources_rag import RagBounds

B = RagBounds()
KW = dict(
    org_max=B.org_max_length,
    project_max=B.project_max_length,
    collection_max=B.collection_max_length,
    wire_max=B.wire_max_length,
)
_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_-"


def _seg(rng: random.Random, hi: int) -> str:
    return "".join(rng.choice(_CHARS) for _ in range(rng.randint(1, hi)))


# Feature: gw31-rag-pipeline, Property 3: namespace wire encoding is injective and round trips
def test_property_injective_and_round_trip() -> None:
    rng = random.Random(3)
    seen: dict[str, tuple[str, str, str]] = {}
    for _ in range(10_000):
        triple = (_seg(rng, 8), _seg(rng, 6), _seg(rng, 6))
        ns = build_namespace(*triple, "project", **KW)
        wire = wire_collection(ns)
        assert wire == wire.lower() and safe_name(wire) == wire
        assert parse_wire(wire) == ns
        assert seen.setdefault(wire, triple) == triple


def test_uppercase_and_bad_chars_refused() -> None:
    for bad in ("AB", "a b", "a/b", "", "é", 5):
        with pytest.raises(NamespaceRefused):
            validate_segment(bad, 32)


def test_org_isolation_fixes_project() -> None:
    ns = build_namespace("o1", "proj", "docs", "org", **KW)
    assert ns.project == "default"


def test_uuid_org_budget() -> None:
    org = "123e4567-e89b-12d3-a456-426614174000"
    build_namespace(org, "default", "a" * 6, "project", **KW)
    with pytest.raises(NamespaceRefused):
        build_namespace(org, "default", "a" * 7, "project", **KW)


def test_worst_case_doubling_refused() -> None:
    with pytest.raises(NamespaceRefused):
        build_namespace("o", "p", "-" * 32, "project", **KW)


def test_parse_wire_rejects_foreign_shapes() -> None:
    for name in ("", "x", "o_s", "oa_sb", "oa_sb_sc_sd", "oA_sb_sc", "oa_xb_sc_sd", "oa_sb_sc_"):
        assert parse_wire(name) is None


def test_encoding_alphabet() -> None:
    assert encode_segment("a_b-c") == "a_ub_dc"


_TRANSPORT_PREFIX = {
    "chroma": 5, "pgvector": 5, "elastic": 5, "milvus": 5, "mongo": 5,
    "weaviate": 5, "redis": 8, "pinecone": 0, "qdrant": 0,
}


def test_transport_prefix_table_covers_every_transport_and_budget_holds() -> None:
    root = Path(__file__).resolve().parents[2] / "gateway_v2/dispatch/vector_transports"
    names = {p.stem.removesuffix("_transport") for p in root.glob("*_transport.py")}
    assert names == set(_TRANSPORT_PREFIX), "a new transport needs a row in the naming table"
    for name in names:
        text = (root / f"{name}_transport.py").read_text()
        for prefix in re.findall(r'"(gw30[_a-z:]*)"|"(Gw30_)"', text):
            literal = prefix[0] or prefix[1]
            assert len(literal) <= _TRANSPORT_PREFIX[name]
    limit_63 = {"chroma", "pgvector"}
    assert B.wire_max_length + max(_TRANSPORT_PREFIX[n] for n in limit_63) <= 63

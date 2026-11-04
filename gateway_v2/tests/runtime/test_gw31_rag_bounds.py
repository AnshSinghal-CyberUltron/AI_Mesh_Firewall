"""GW31 RagBounds."""
from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_rag import RagBounds


def _contract() -> ResourceContract:
    from tests.runtime.test_gw17_mcp_bounds import _contract as make

    return make()


def test_defaults_positive_and_threshold_above_half() -> None:
    b = _contract().rag_bounds()
    assert 0.5 < b.default_relevance <= 1.0
    assert b.wire_max_length == 58
    assert (b.org_max_length, b.project_max_length, b.collection_max_length) == (36, 32, 32)
    assert b.max_documents <= _contract().vector_max_result_count()


def test_body_never_looser_than_chat() -> None:
    c = _contract()
    assert c.rag_bounds().body_bytes <= c.max_request_bytes()


def test_store_bounds_positive() -> None:
    c = _contract()
    assert c.rag_chunk_store_entries() > 0 and c.rag_chunk_store_bytes() > 0
    assert c.rag_max_documents_ceiling() == c.rag_bounds().max_documents


def test_non_positive_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import gateway_v2.runtime.resources_rag as mod

    bad = dataclasses.replace(RagBounds(), canary_bytes=0)
    monkeypatch.setattr(mod, "RagBounds", lambda: bad)
    with pytest.raises(CapacityUnavailable):
        _contract().rag_bounds()

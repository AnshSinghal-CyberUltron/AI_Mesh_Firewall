"""RAG responses must NOT leak firewall-internal (underscore-prefixed) doc fields
to the client: _content_hash (membership inference), _trust_score (ranking leak),
_doc_id. Public id/content/metadata/score are preserved.
"""
import main


def test_strip_removes_underscore_fields_keeps_public():
    docs = [{
        "id": "d1", "content": "text", "score": 0.9, "distance": 0.1,
        "metadata": {"source": "policy"},
        "_doc_id": "d1", "_content_hash": "abc123", "_trust_score": 0.87, "_relevance": 0.9,
    }]
    main._strip_internal_doc_fields(docs)
    d = docs[0]
    assert "_doc_id" not in d and "_content_hash" not in d
    assert "_trust_score" not in d and "_relevance" not in d
    # public fields preserved
    assert d["id"] == "d1" and d["content"] == "text"
    assert d["score"] == 0.9 and d["metadata"] == {"source": "policy"}


def test_strip_handles_non_dict_and_non_list():
    main._strip_internal_doc_fields(None)          # no-op, no crash
    main._strip_internal_doc_fields("x")           # no-op
    docs = ["not-a-dict", {"_x": 1, "id": "k"}]
    main._strip_internal_doc_fields(docs)
    assert docs[0] == "not-a-dict"
    assert "_x" not in docs[1] and docs[1]["id"] == "k"


def test_strip_empty_and_no_internal_fields():
    docs = [{"id": "d", "content": "c", "score": 0.5}]
    main._strip_internal_doc_fields(docs)
    assert docs == [{"id": "d", "content": "c", "score": 0.5}]

"""#19 (verified-sound, +coverage): a tenant must NOT be able to inflate its own
docs' ranker trust score by self-asserting the platform trust signals
``verified_source`` / ``created_by=system`` in ingest metadata (Ranking Poisoning /
trust spoofing). The /v1/rag/ingest handler strips these keys via
main._strip_platform_trust_metadata (main.py ~11737). This control had no executed
assertion; these tests lock it.
"""
import main
from rag_pipeline.ranker_stage import compute_trust_score


def test_platform_trust_signals_inflate_trust_score_when_present():
    """The RISK the strip mitigates: these keys add up to +0.15 trust."""
    doc_plain = {"distance": 0.9, "metadata": {}}
    doc_spoof = {"distance": 0.9, "metadata": {"verified_source": True, "created_by": "system"}}
    base = compute_trust_score(doc_plain, {})
    spoof = compute_trust_score(doc_spoof, {})
    assert spoof > base
    assert round(spoof - base, 3) == 0.15


def test_strip_removes_platform_trust_keys():
    meta = {"verified_source": True, "created_by": "system", "author": "alice", "topic": "refunds"}
    stripped = main._strip_platform_trust_metadata(meta)
    assert "verified_source" not in stripped
    assert "created_by" not in stripped
    # Legitimate metadata is preserved untouched.
    assert stripped["author"] == "alice"
    assert stripped["topic"] == "refunds"


def test_strip_neutralizes_the_inflation_vector():
    """After the ingest strip, the same spoof metadata yields NO trust bonus."""
    spoof_meta = {"verified_source": True, "created_by": "system"}
    stripped = main._strip_platform_trust_metadata(spoof_meta)
    base = compute_trust_score({"distance": 0.9, "metadata": {}}, {})
    after = compute_trust_score({"distance": 0.9, "metadata": stripped}, {})
    assert after == base  # inflation gone


def test_strip_passes_through_non_dict():
    assert main._strip_platform_trust_metadata(None) is None
    assert main._strip_platform_trust_metadata("x") == "x"

"""#18: SemanticLeakageDetector._confidential_fingerprints grew UNBOUNDED.

register_confidential_content is called once per retrieved doc per RAG query
(generator_stage.py:261) and previously never evicted — so the in-memory dict
grew for the whole process lifetime (a slow memory leak). Worse, on the RAG
pipeline's detector instance the entries are write-only: check_leakage is only
ever called on the OUTPUT_GUARD's SEPARATE instance (output_guard.py:1033), so
the RAG registrations are never even read. These tests lock the FIFO cap that
stops the unbounded growth.
"""
from leakage_detector import SemanticLeakageDetector


def test_fingerprint_store_is_bounded_fifo():
    d = SemanticLeakageDetector(redis_client=None, max_registered_docs=10)
    for i in range(50):
        d.register_confidential_content(
            f"doc{i}", f"some confidential content number {i} with enough tokens to fingerprint"
        )
    # Never exceeds the cap.
    assert len(d._confidential_fingerprints) == 10
    # Oldest evicted (FIFO): doc0..doc39 gone, doc40..doc49 kept.
    assert "doc0" not in d._confidential_fingerprints
    assert "doc39" not in d._confidential_fingerprints
    assert "doc40" in d._confidential_fingerprints
    assert "doc49" in d._confidential_fingerprints


def test_reregister_same_doc_id_does_not_grow():
    d = SemanticLeakageDetector(redis_client=None, max_registered_docs=100)
    for _ in range(20):
        d.register_confidential_content("same", "confidential content with several tokens here for ngrams")
    assert len(d._confidential_fingerprints) == 1


def test_bound_disabled_when_non_positive():
    d = SemanticLeakageDetector(redis_client=None, max_registered_docs=0)
    for i in range(30):
        d.register_confidential_content(f"d{i}", "content tokens here for fingerprint generation ok")
    assert len(d._confidential_fingerprints) == 30


def test_default_cap_is_bounded():
    # The production default must be a finite bound (not unbounded).
    d = SemanticLeakageDetector(redis_client=None)
    assert d._max_registered_docs > 0

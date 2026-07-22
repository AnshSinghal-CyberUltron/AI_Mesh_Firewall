"""If the PII redactor CRASHES, the query stage must NOT embed the fully-raw
query ("never embed unredacted content"): it falls through to the digit backstop
so 7+-digit phone/SSN/ID runs are still masked, and warns (observable).
"""
import logging
from dataclasses import dataclass, field
import pytest
from rag_pipeline.query_stage import QueryStage
from rag_pipeline.contracts import QueryStageInput


@dataclass
class _V:
    action: str = "allow"; threat_type: str = ""; confidence: float = 0.0
    detail: str = ""; matched_patterns: list = field(default_factory=list); tier: str = "tier_1"


class _CrashRedactScanner:
    async def scan_prompt(self, text, is_rag=False):
        return _V()
    def redact_pii(self, text, verdict=None):
        raise RuntimeError("redactor exploded")


def _inp(q):
    return QueryStageInput(query_text=q, collection_name="docs", project_id="o1",
        vector_db_type="pinecone", n_results=5, where_filter=None, namespace="",
        policy={}, key_hash="")


async def test_redaction_crash_still_masks_digits(caplog):
    stage = QueryStage(_CrashRedactScanner(), config={"input_scan_enabled": True})
    with caplog.at_level(logging.WARNING, logger="gateway.rag_pipeline.query_stage"):
        out = await stage.execute(_inp("my number is 5551234567 ok"))
    # Fail-open (not blocked / crashed) but fail-SAFE (raw phone NOT embedded).
    assert out.verdict.action in ("allow", "flag")
    assert "5551234567" not in out.sanitized_query, "raw 7+digit run embedded on redactor crash"
    assert "***-***-4567" in out.sanitized_query
    assert any("redaction failed" in r.getMessage().lower() for r in caplog.records)

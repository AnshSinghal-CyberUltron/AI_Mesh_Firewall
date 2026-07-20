"""SCAN CONTROL disable-semantics: 'if disabled, it MUST NOT execute'.

Drives the real RAGFirewallPipeline with a ContextGuard stub that RECORDS which
scan methods actually run, and asserts the config/policy toggles gate them.
Also pins one asymmetry: rag_context_scan_enabled=False disables the batch
content scan (scan_documents) but NOT per-doc sensitive filtering
(scan_single_document), which is governed by the separate block_sensitive_documents
policy (default True) — documented so the semantics are explicit, not surprising.
"""
from __future__ import annotations

import pytest

from rag_pipeline import RAGFirewallPipeline


class _V:
    def __init__(self, action="allow", threat_type="", confidence=0.0, detail="",
                 matched_patterns=None, flagged_documents=None):
        self.action = action
        self.threat_type = threat_type
        self.confidence = confidence
        self.detail = detail
        self.matched_patterns = matched_patterns or []
        self.flagged_documents = flagged_documents or []


class _RecordingGuard:
    def __init__(self):
        self.calls: list[str] = []

    def detect_embedding_anomaly(self, distances, threshold):
        self.calls.append("detect_embedding_anomaly")
        return []

    async def scan_documents(self, documents, query_text):
        self.calls.append("scan_documents")
        return _V(action="allow")

    async def scan_single_document(self, content):
        self.calls.append("scan_single_document")
        return _V(action="allow")


class _Client:
    async def query(self, collection_name, query_text, n_results, where, namespace, project_id):
        return [{"id": "d1", "content": "Refund policy: 30 days.", "score": 0.9, "distance": 0.1}]


def _pipe(guard, config):
    cfg = {"rag_ranker_enabled": True, "rag_generator_enabled": False}
    cfg.update(config)
    return RAGFirewallPipeline(
        input_scanner=None, context_guard=guard, leakage_detector=None,
        vector_clients={}, circuit_breaker=None, rate_limiter=None,
        telemetry=None, redis_client=None, config=cfg,
    )


async def _run(guard, config, policy=None):
    p = _pipe(guard, config)
    await p.execute(
        query_text="what is the refund policy", collection_name="docs",
        project_id="org1-default", vector_db_type="pinecone", n_results=5,
        vector_client_override=_Client(), policy=policy or {},
    )
    return guard.calls


# ── Content scan (scan_documents) is gated by rag_context_scan_enabled ──
async def test_context_scan_disabled_skips_scan_documents():
    calls = await _run(_RecordingGuard(), {"rag_context_scan_enabled": False},
                       policy={"block_sensitive_documents": False})
    assert "scan_documents" not in calls


async def test_context_scan_enabled_runs_scan_documents():
    calls = await _run(_RecordingGuard(), {"rag_context_scan_enabled": True},
                       policy={"block_sensitive_documents": False})
    assert "scan_documents" in calls


# ── Anomaly detection is gated by rag_anomaly_detection_enabled ──
async def test_anomaly_disabled_skips_detect_embedding_anomaly():
    calls = await _run(_RecordingGuard(), {"rag_anomaly_detection_enabled": False},
                       policy={"block_sensitive_documents": False})
    assert "detect_embedding_anomaly" not in calls


async def test_anomaly_enabled_runs_detect_embedding_anomaly():
    calls = await _run(_RecordingGuard(), {"rag_anomaly_detection_enabled": True},
                       policy={"block_sensitive_documents": False})
    assert "detect_embedding_anomaly" in calls


# ── Sensitive filtering: separate control (block_sensitive_documents policy) ──
async def test_sensitive_filtering_runs_even_when_content_scan_disabled():
    # Documents the asymmetry: content scan OFF, but block_sensitive_documents
    # defaults True, so per-doc sensitive filtering STILL runs.
    calls = await _run(_RecordingGuard(), {"rag_context_scan_enabled": False}, policy={})
    assert "scan_single_document" in calls
    assert "scan_documents" not in calls


async def test_sensitive_filtering_disabled_via_policy():
    # The documented way to fully stop per-doc sensitive scanning.
    calls = await _run(_RecordingGuard(), {"rag_context_scan_enabled": False},
                       policy={"block_sensitive_documents": False})
    assert "scan_single_document" not in calls

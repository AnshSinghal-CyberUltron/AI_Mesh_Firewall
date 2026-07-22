"""#16: the RAG pipeline's per-stage telemetry must honor the caller's per-org
audit gate (telemetry_enabled).

Previously `_emit_stage_telemetry` emitted whenever the GLOBAL sink existed,
ignoring the per-org `telemetry_enabled` that the chat path's `_emit_telemetry`
honors (main.py `_org_audit_logging_enabled`). So an org that turned audit
logging OFF still had `rag_pipeline` per-stage events (carrying verdict detail,
collection, namespace) written to the audit sink — a config→runtime asymmetry.
Root cause was twofold: the pipeline had no gate, AND the RAG handler never set
`_REQUEST_ORG_SLUG`, so even the handler-level gate fell back to the global.
Fix: `execute(telemetry_enabled=...)` → `ctx.telemetry_enabled` →
`_emit_stage_telemetry` suppresses when False; the handler resolves the per-org
value and sets the org ContextVars.
"""
from rag_pipeline import RAGFirewallPipeline


class _RecordingTelemetry:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class _StubVectorClient:
    def __init__(self, docs):
        self._docs = docs

    async def query(self, **kw):
        return list(self._docs)


def _pipeline(telemetry):
    return RAGFirewallPipeline(
        input_scanner=None, context_guard=None, leakage_detector=None,
        vector_clients={}, circuit_breaker=None, rate_limiter=None,
        telemetry=telemetry, redis_client=None, config={},
    )


async def _run(pipe, client, **kw):
    base = dict(
        query_text="what is the refund policy",
        collection_name="docs",
        project_id="org1-default",
        vector_db_type="pinecone",
        n_results=5,
        vector_client_override=client,
    )
    base.update(kw)
    return await pipe.execute(**base)


_DOCS = [{"id": "d1", "content": "Refunds within 30 days.", "score": 0.9}]


async def test_pipeline_telemetry_suppressed_when_org_disabled():
    tel = _RecordingTelemetry()
    await _run(_pipeline(tel), _StubVectorClient(_DOCS), telemetry_enabled=False)
    assert tel.events == [], (
        f"expected NO per-stage telemetry when org disabled audit logging, "
        f"got {len(tel.events)}"
    )


async def test_pipeline_telemetry_emitted_when_org_enabled():
    tel = _RecordingTelemetry()
    await _run(_pipeline(tel), _StubVectorClient(_DOCS), telemetry_enabled=True)
    assert len(tel.events) > 0, "expected per-stage telemetry when audit logging ON"


async def test_pipeline_telemetry_default_enabled_backward_compat():
    # Omitting telemetry_enabled must keep emitting (no-org/legacy callers).
    tel = _RecordingTelemetry()
    await _run(_pipeline(tel), _StubVectorClient(_DOCS))
    assert len(tel.events) > 0, "default must remain enabled for backward compat"

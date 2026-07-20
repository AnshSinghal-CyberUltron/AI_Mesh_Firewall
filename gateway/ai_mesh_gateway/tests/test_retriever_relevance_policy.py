"""The retriever must honor a PER-REQUEST (per-org) rag_relevance_threshold from
policy, not only the static startup config — otherwise a per-org threshold set in
the frontend is silently ignored (config->runtime mismatch).
"""
from rag_pipeline import RAGFirewallPipeline


class _Client:
    async def query(self, **kw):
        return [
            {"id": "hi", "content": "good match", "score": 0.9},
            {"id": "lo", "content": "weak match", "score": 0.15},
        ]


def _pipe(config):
    return RAGFirewallPipeline(None, None, None, {}, None, None, None, None, config=config)


async def _run(config, policy):
    r = await _pipe(config).execute(
        query_text="q", collection_name="docs", project_id="org1",
        vector_db_type="pinecone", n_results=5, vector_client_override=_Client(), policy=policy)
    return {d["id"] for d in r.documents}


async def test_policy_threshold_applied_over_static_off():
    # static config OFF (0.0); per-org policy sets 0.5 -> low-score doc dropped.
    ids = await _run({}, {"rag_relevance_threshold": 0.5})
    assert ids == {"hi"}, f"per-org relevance threshold ignored: {ids}"


async def test_static_used_when_policy_omits():
    # no policy value + static OFF -> both kept (unchanged fallback behavior).
    ids = await _run({}, {})
    assert ids == {"hi", "lo"}


async def test_policy_overrides_static_on():
    # static ON at strict 0.99 would drop both; policy relaxes to 0.5 -> keep hi.
    ids = await _run({"rag_relevance_threshold": 0.99}, {"rag_relevance_threshold": 0.5})
    assert ids == {"hi"}


async def test_malformed_policy_value_falls_back():
    ids = await _run({"rag_relevance_threshold": 0.5}, {"rag_relevance_threshold": "nan-ish"})
    assert ids == {"hi"}  # falls back to static 0.5

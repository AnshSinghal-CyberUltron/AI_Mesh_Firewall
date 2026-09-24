"""§10.1.5 row 4: /v1/vector/query can never run Tier-2.

Drives the REAL vector_routes._build_rag_policy (fed by a fake CONFIG_SYNC whose
org has rag_tier2_enabled=True) into the REAL QueryStage with a recording scanner,
and contrasts with the key set main.py's /v1/rag/query copies (main.py:13878-13896).
"""
import asyncio
import sys
import types

import vector_routes
from rag_pipeline.query_stage import QueryStage
from rag_pipeline.contracts import QueryStageInput
from config import load_config

ORG_CFG = {  # what config_sync holds for the org (control-plane payload keys)
    "rag_tier2_enabled": True, "tier2_strict": True, "rag_relevance_threshold": 0.7,
    "prompt_injection_threshold": 0.8, "prompt_rewrite_threshold": 0.6, "prompt_downgrade_threshold": 0.4,
}


class FakeConfigSync:
    def get_config(self, slug=""):
        return dict(ORG_CFG)


class RecordingScanner:
    def __init__(self):
        self.calls = []

    async def scan_prompt(self, text, is_rag=False, **kw):
        self.calls.append("scan_prompt(Tier-1)")
        from scanner import ScanVerdict
        return ScanVerdict(action="allow", threat_type="", tier="tier_1")

    async def scan_prompt_with_tier2(self, text, **kw):
        self.calls.append("scan_prompt_with_tier2(Tier-2)")
        from scanner import ScanVerdict
        return ScanVerdict(action="allow", threat_type="", tier="tier_2")


async def run(label, policy):
    sc = RecordingScanner()
    qs = QueryStage(scanner=sc, config=load_config(), llm_judge=None)
    inp = QueryStageInput(query_text="What is our refund policy?", collection_name="kb", project_id="p",
                          vector_db_type="chroma", n_results=3, where_filter=None, namespace="",
                          policy=policy, key_hash="k")
    await qs.execute(inp)
    print(f"{label:34} policy keys={sorted(policy)}\n{'':34} scanner calls={sc.calls}")


async def main():
    fake_main = types.SimpleNamespace(CONFIG_SYNC=FakeConfigSync())
    sys.modules["ai_mesh_gateway.main"] = fake_main  # _gateway_main_module() resolves this first
    print("_RAG_GUARDRAIL_KEYS =", vector_routes._RAG_GUARDRAIL_KEYS)
    vec_policy = vector_routes._build_rag_policy("acme")
    await run("/v1/vector/query (_build_rag_policy)", vec_policy)
    rag_policy = {"_org_slug": "acme"}
    for k in ("prompt_injection_threshold", "prompt_rewrite_threshold", "prompt_downgrade_threshold",
              "rag_relevance_threshold", "rag_tier2_enabled", "tier2_strict"):  # main.py:13878-13896
        if k in ORG_CFG:
            rag_policy[k] = ORG_CFG[k]
    await run("/v1/rag/query (main.py key set)", rag_policy)
    print("control-plane payload emits input_scan_enabled? ->",
          "no (removed in policy-driven-detection task 6.2; see control core/models.py build_gateway_payload)")


asyncio.run(main())

# Reviewer A's probe (PR #115 review 5869763490); fp/unavailable import switched to tests.edge.test_f4_route_surfaces. Run from gateway_v2/ with venv-ci.
"""Reviewer A #115: is REWRITE ignored only at the RAG generator (R15), or on every RAG/vector path?"""
import asyncio, json, tempfile
from pathlib import Path
from tests.edge.gw31_harness import build_app, call, rule, write_plan, ingest_and_wait

def plan(tmp, scope):
    r = rule("rw", ("zebracode",), action="rewrite", scope=scope, surfaces=["rag", "vector"])
    r["rewrite_template"] = "[policy removed]"
    p = tmp / "plan.json"; write_plan(p, rules=[r]); return p

async def main():
    tmp = Path(tempfile.mkdtemp())
    app, deps = build_app(plan(tmp, "input"))
    job = await ingest_and_wait(app, "docs", [{"id": "d1", "text": "refund policy zebracode here", "source": "s"}])
    got = await deps["rag"].chunk_store.get_many("odev_dorg_sdefault_sdocs", ("d1",))
    print("input-scope rewrite, ingest: job", job.get("state"), "stored:", [c.text for c in got.values()])
    st, body, raw = await call(app, "POST", "/v1/rag/query", {"collection": "docs", "query": "refund policy zebracode"})
    print("input-scope rewrite, rag query:", st, [s for s in body.get("trace", []) if s.get("stage") in ("query", "retriever")], "context:", [c["text"] for c in body.get("context", [])])
    vec = (await deps["rag"].embedder.embed("dev-org", ("refund policy zebracode here",)))[0]
    st, body, raw = await call(app, "POST", "/v1/vector/query", {"collection": "docs", "vector": list(vec)})
    print("input-scope rewrite, vector query:", st, [m["text"] for m in body.get("matches", [])])

asyncio.run(main())

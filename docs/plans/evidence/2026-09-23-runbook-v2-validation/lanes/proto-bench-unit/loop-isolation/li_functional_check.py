"""Functional checks of the li knobs on the li tree (run with PYTHONPATH=<li-tree>)."""
import json, sys
import orjson
from rvproto.detect.semantic import SemanticDetector
from rvproto.detect.matcher import Matcher
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.edge.inspect import PlanInspector
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.egress.stream import StreamPipeline
from rvproto.runtime.metrics import Registry
from rvproto.runtime.config import load_settings

class _G:  # guard stub: windows() only needs the tokenizer
    model_hash = "x"
sem = SemanticDetector(sys.argv[1], _G(), window=512, overlap=64, max_windows=16, model_hash="x")
rows = [json.loads(l) for l in open(sys.argv[2])][:400]
same = 0
for r in rows:
    texts = [m["content"].replace("{{RVNONCE}}", "ref-w1-alpha-bravo-charlie-delta") for m in r["messages"]]
    a, b = sem.windows(texts), sem.windows(texts, True)
    same += (a.rows == b.rows and a.n_tokens == b.n_tokens)
print(f"encode vs encode_batch windows identical: {same}/{len(rows)}")
m = Matcher(); d = DeterministicDetectors(m); plan = compile_plan(org_a())
toks = [" hello", " world", ".", " mail", " bob", "@exa", "mple.com", " now", " key", " AKIA", "IOSFODNN7EXAMPLE", " end"]
for hb in (True, False):
    pipe = StreamPipeline(PlanInspector(m, d, plan), Registry(0), ceiling=1 << 20, inject_hold=None, holdback=hb)
    out = []
    for i, t in enumerate(toks):
        ch = {"id": "x", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]}
        out.append("".join(orjson.loads(f[6:])["choices"][0]["delta"].get("content", "") for f in pipe._process(ch, pipe.stats, i, [])))
    print(f"holdback={hb}: released per token {out}  scans={pipe.stats.scans}")
for env in ({}, {"RV_METRICS_DUMP_S": "0", "RV_TOKENIZE_IN_THREAD": "1", "RV_HOLDBACK": "off"}):
    s = load_settings(env)
    print(env or "defaults", "->", s.metrics_dump_s, s.tokenize_in_thread, s.tokenize_threads, s.holdback)

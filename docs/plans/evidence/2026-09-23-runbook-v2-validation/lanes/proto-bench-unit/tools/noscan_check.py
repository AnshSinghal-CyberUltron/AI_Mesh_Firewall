"""Functional check of RV_EXP_OUTPUT_SCAN: same token stream through PlanInspector vs NoScanInspector."""
import orjson
from rvproto.detect.matcher import Matcher
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.edge.inspect import PlanInspector, NoScanInspector
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.egress.stream import StreamPipeline
from rvproto.runtime.metrics import Registry
from rvproto.runtime.config import load_settings
m = Matcher(); d = DeterministicDetectors(m); plan = compile_plan(org_a())
toks = [" hello", " world", ".", " mail", " me", " at", " bob@example.com", " now"]
for cls in (PlanInspector, NoScanInspector):
    pipe = StreamPipeline(cls(m, d, plan), Registry(0), ceiling=1 << 20, inject_hold=None)
    out = []
    for i, t in enumerate(toks):
        ch = {"id": "x", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]}
        frames = pipe._process(ch, pipe.stats, i * 20_000_000, [])
        out.append("".join(orjson.loads(f[6:])["choices"][0]["delta"].get("content", "") for f in frames))
    print(f"{cls.__name__:16s} released per upstream token: {out}")
print("settings default exp_output_scan:", load_settings({}).exp_output_scan, "| off:", load_settings({"RV_EXP_OUTPUT_SCAN": "off"}).exp_output_scan)
try:
    load_settings({"RV_EXP_OUTPUT_SCAN": "maybe"})
except ValueError as e:
    print("invalid value rejected:", e)

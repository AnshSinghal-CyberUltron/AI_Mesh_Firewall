"""P12 (GW13/GW09): tool-call `arguments` is a JSON document inside a JSON string.  rvproto scans the raw
arguments text; a JSON escape inside it (\\u0041 = 'A', \\u0040 = '@') hides the value from every detector, yet the
tool / SDK consumer that json.loads(arguments) gets the secret back.  Input (org-a secret->BLOCK, PII->REDACT) and
output (org-a output secret/PII->REDACT) paths, benchmarked source."""
import asyncio, json, sys
import orjson
sys.path.insert(0, sys.argv[1])
from rvproto.edge.wire import parse_chat
from rvproto.detect.canon import canonicalize
from rvproto.detect.matcher import Matcher
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.edge.inspect import PlanInspector
from rvproto.egress.stream import StreamPipeline
from rvproto.egress.jsonout import inspect_json
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.resolve.resolver import resolve
from rvproto.domain.plan import Phase
from rvproto.domain.findings import Finding, FindingStatus, DETECTORS, SEMANTIC_DETECTOR
from rvproto.runtime.metrics import Registry
KEY, MAIL = "AKIAQYLPMN5HHHFPZAM2", "alice.canary@example.com"
plan = compile_plan(org_a()); det = DeterministicDetectors(Matcher())
sem = Finding(SEMANTIC_DETECTOR, "v", DETECTORS[SEMANTIC_DETECTOR], FindingStatus.EXECUTED, 0.0, (), None)
def esc(s):  # JSON-escape the first char (what a JSON encoder with ensure_ascii-like escaping may emit)
    return "\\u%04x" % ord(s[0]) + s[1:]
args_plain = json.dumps({"key": KEY, "mail": MAIL})
args_esc = '{"key": "%s", "mail": "%s"}' % (esc(KEY), MAIL.replace("@", "\\u0040"))
assert json.loads(args_esc) == {"key": KEY, "mail": MAIL}
print("== INPUT: assistant tool_calls[].function.arguments ==")
for name, args in (("plain", args_plain), ("JSON-escaped", args_esc)):
    chat = parse_chat(orjson.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": args}}]},
        {"role": "tool", "tool_call_id": "c", "content": "ok"}]}))
    dec = resolve((*det.scan([canonicalize(s.text, 4096) for s in chat.segments], plan.required_input), sem), plan, Phase.INPUT)
    print(f"  {name:<13} disposition={dec.disposition.value:<6} consumer json.loads(arguments) -> {json.loads(args)}")
ins = PlanInspector(Matcher(), det, plan)
print("== OUTPUT (non-stream): message.tool_calls[].function.arguments ==")
for name, args in (("plain", args_plain), ("JSON-escaped", args_esc)):
    raw = orjson.dumps({"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function",
        "function": {"name": "f", "arguments": args}}]}, "finish_reason": "tool_calls"}]})
    jo = inspect_json(raw, ins)
    got = json.loads(orjson.loads(jo.body)["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
    print(f"  {name:<13} disposition={jo.decision.disposition.value:<6} client json.loads(arguments) -> {got}")
print("== OUTPUT (SSE): arguments fragments ==")
async def sse(args):
    frags = [args[i:i + 7] for i in range(0, len(args), 7)]
    def fr(d, fin=None):
        return b"data: " + orjson.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
               "choices": [{"index": 0, "delta": d, "finish_reason": fin}]}) + b"\n\n"
    chunks = [fr({"role": "assistant", "tool_calls": [{"index": 0, "id": "c", "type": "function", "function": {"name": "f", "arguments": ""}}]})]
    chunks += [fr({"tool_calls": [{"index": 0, "function": {"arguments": f}}]}) for f in frags] + [fr({}, "tool_calls"), b"data: [DONE]\n\n"]
    class R:
        class content:
            @staticmethod
            async def iter_any():
                for c in chunks: yield c
    out = []
    async def send(b): out.append(b)
    await StreamPipeline(ins, Registry(0), ceiling=10**9, inject_hold=None).run(R(), send)
    acc = ""
    for line in b"".join(out).split(b"\n\n"):
        if line.startswith(b"data: {"):
            for ch in orjson.loads(line[6:]).get("choices", []):
                for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                    acc += (tc.get("function") or {}).get("arguments") or ""
    return json.loads(acc)
for name, args in (("plain", args_plain), ("JSON-escaped", args_esc)):
    print(f"  {name:<13} client json.loads(assembled arguments) -> {asyncio.run(sse(args))}")

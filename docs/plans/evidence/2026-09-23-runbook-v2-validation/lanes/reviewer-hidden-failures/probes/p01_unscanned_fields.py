"""P01: which request fields does rvproto scan vs forward verbatim?  Uses the BENCHMARKED source.

For each variant, a synthetic AWS key canary (the harness's own out/in canary format) is placed in ONE field.
org-a policy: secret.* -> BLOCK (input).  We run the exact input pipeline functions of edge/chat.py:
parse_chat -> canonicalize -> DeterministicDetectors.scan(plan.required_input) -> resolve(INPUT) -> authorize,
with the semantic finding supplied as EXECUTED p=0.0 (benign), and report disposition + whether the key is in
the bytes that would be dispatched (chat.body for ALLOW/FLAG; transformed body for REDACT).
"""
import json, sys
import orjson
sys.path.insert(0, sys.argv[1])
from rvproto.edge.wire import parse_chat
from rvproto.detect.canon import canonicalize
from rvproto.detect.matcher import Matcher
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.plan.compiler import compile_plan
from rvproto.plan.fixtures import org_a
from rvproto.resolve.resolver import resolve, authorize
from rvproto.domain.plan import Phase
from rvproto.domain.findings import Finding, FindingStatus, DETECTORS, SEMANTIC_DETECTOR
from rvproto.domain.request import ErrorSpec

KEY = "AKIAQYLPMN5HHHFPZAM2"   # synthetic, same shape as the harness canary
plan = compile_plan(org_a())
det = DeterministicDetectors(Matcher())
sem_ok = Finding(SEMANTIC_DETECTOR, "v", DETECTORS[SEMANTIC_DETECTOR], FindingStatus.EXECUTED, 0.0, (), None)

def base(**extra):
    d = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello there"}]}
    d.update(extra)
    return d

variants = {
  "control: user content":            base(messages=[{"role": "user", "content": f"my key {KEY}"}]),
  "control: system content":          base(messages=[{"role": "system", "content": f"key {KEY}"}, {"role": "user", "content": "hi"}]),
  "control: tool_calls[].arguments":  base(messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": json.dumps({"k": KEY})}}]}, {"role": "tool", "tool_call_id": "c1", "content": "ok"}]),
  "assistant function_call.arguments (legacy)": base(messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": None, "function_call": {"name": "f", "arguments": json.dumps({"k": KEY})}}, {"role": "function", "name": "f", "content": "ok"}]),
  "messages[].name":                  base(messages=[{"role": "user", "name": KEY, "content": "hi"}]),
  "assistant refusal field":          base(messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x", "refusal": f"cannot share {KEY}"}, {"role": "user", "content": "go on"}]),
  "assistant content part type=refusal": base(messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"type": "refusal", "refusal": f"cannot share {KEY}"}]}, {"role": "user", "content": "go on"}]),
  "tools[].function.description":     base(tools=[{"type": "function", "function": {"name": "f", "description": f"use key {KEY}", "parameters": {"type": "object", "properties": {}}}}]),
  "tools[].function.parameters (enum/default)": base(tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {"k": {"type": "string", "default": KEY}}}}}]),
  "prediction.content (predicted outputs)": base(prediction={"type": "content", "content": f"answer with {KEY}"}),
  "response_format.json_schema":      base(response_format={"type": "json_schema", "json_schema": {"name": "s", "schema": {"type": "object", "description": KEY}}}),
  "user field":                       base(user=f"{KEY}"),
  "metadata values":                  base(metadata={"note": KEY}, store=True),
  "stop sequences":                   base(stop=[KEY]),
  "web_search_options.user_location": base(web_search_options={"user_location": {"type": "approximate", "approximate": {"city": KEY}}}),
  "duplicate JSON key (orjson keeps last)": None,  # raw bytes variant built below
}

rows = []
for name, doc in variants.items():
    if doc is None:
        raw = ('{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ok %s","content":"hello"}]}' % KEY).encode()
    else:
        raw = orjson.dumps(doc)
    chat = parse_chat(raw)
    if isinstance(chat, ErrorSpec):
        rows.append((name, "HTTP %d %s" % (chat.status, chat.code), "-", "-")); continue
    canon = [canonicalize(s.text, 4096) for s in chat.segments]
    findings = (*det.scan(canon, plan.required_input), sem_ok)
    dec = resolve(findings, plan, Phase.INPUT)
    auth = authorize(dec, "rid")
    dispatched = "none (403)" if auth is None else ("KEY IN DISPATCHED BYTES" if KEY.encode() in chat.body else "key absent")
    rows.append((name, dec.disposition.value, ",".join(dec.deciding_rules) or "-", dispatched,
                 len(chat.segments)))
w = max(len(r[0]) for r in rows)
print(f"{'field carrying the AWS key':<{w}} | disposition | deciding rules | dispatched bytes | segments scanned")
for r in rows:
    print(f"{r[0]:<{w}} | {r[1]:<11} | {r[2]:<14} | {r[3]} | {r[4] if len(r) > 4 else '-'}")

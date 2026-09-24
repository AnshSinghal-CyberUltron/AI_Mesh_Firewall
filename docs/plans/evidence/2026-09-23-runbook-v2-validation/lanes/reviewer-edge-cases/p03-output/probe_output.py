"""P03: output-path edge cases through rvproto (org-a: output secrets+PII -> REDACT).

For each scripted upstream scenario, capture the exact client-received bytes (raw HTTP) and
check (1) does any canary reach the client raw, anywhere in the bytes (content, logprobs, refusal,
reasoning, function_call, error frames); (2) is the reassembled content / tool-call arguments /
structured output still valid JSON; (3) does the official OpenAI SDK (2.38.0) accept the result.
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import httpx
import openai

BASE = "http://127.0.0.1:8480"
KEY_A = "sk-rv-org-a-0001"
AWS, EMAIL, CARD = "AKIAQYLPMN5HHHFPZAM2", "alice.canary@example.com", "4111111111111111"
CANARIES = {"aws": AWS, "email": EMAIL, "card": CARD, "phone": "2025550143"}


def raw_call(scen: str, stream: bool, extra: dict | None = None, hdr: dict | None = None) -> dict:
    rid = f"p03-{scen}-{'s' if stream else 'j'}-{uuid.uuid4().hex[:8]}"
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello there"}], "stream": stream}
    body.update(extra or {})
    h = {"authorization": f"Bearer {KEY_A}", "x-request-id": rid, "x-synth-edge": scen}
    h.update(hdr or {})
    t0 = time.perf_counter()
    with httpx.Client(timeout=120) as c:
        r = c.post(f"{BASE}/v1/chat/completions", json=body, headers=h)
        data = r.content
    dt = time.perf_counter() - t0
    text = data.decode("utf-8", "replace")
    out = {"scenario": scen, "stream": stream, "rid": rid, "status": r.status_code, "elapsed_s": round(dt, 3),
           "x-rv-disposition": r.headers.get("x-rv-disposition"), "x-rv-output": r.headers.get("x-rv-output"),
           "x-rv-stages": r.headers.get("x-rv-stages"), "bytes": len(data)}
    out["canaries_in_raw_bytes"] = [k for k, v in CANARIES.items() if v in text]
    stripped = text.replace("​", "").replace("\\u200b", "")
    out["canaries_after_zwsp_strip"] = [k for k, v in CANARIES.items() if v in stripped]
    if stream:
        frames, content, reasoning, refusal, fn_args, tool_args, errors = [], {}, "", "", "", {}, []
        logprob_tokens = []
        for ev in text.split("\n\n"):
            if not ev.startswith("data: "):
                continue
            p = ev[6:]
            if p == "[DONE]":
                frames.append("[DONE]")
                continue
            try:
                d = json.loads(p)
            except json.JSONDecodeError:
                errors.append("bad frame json")
                continue
            frames.append(d)
            if "error" in d:
                errors.append(d["error"])
            for ch in d.get("choices") or []:
                dl = ch.get("delta") or {}
                i = ch.get("index", 0)
                content[i] = content.get(i, "") + (dl.get("content") or "")
                reasoning += dl.get("reasoning_content") or ""
                refusal += dl.get("refusal") or ""
                fn_args += (dl.get("function_call") or {}).get("arguments") or ""
                for tc in dl.get("tool_calls") or []:
                    k = (i, tc.get("index", 0))
                    tool_args[k] = tool_args.get(k, "") + ((tc.get("function") or {}).get("arguments") or "")
                for lpc in ((ch.get("logprobs") or {}).get("content") or []):
                    logprob_tokens.append(lpc.get("token"))
        out.update(done=frames[-1:] == ["[DONE]"], n_frames=len(frames), error_frames=errors,
                   content=content, reasoning=reasoning, refusal=refusal, fn_args=fn_args,
                   tool_args={f"{a}:{b}": v for (a, b), v in tool_args.items()},
                   logprob_tokens=logprob_tokens)
        for name, s in [("fn_args", fn_args)] + [(f"tool_args[{k}]", v) for k, v in out["tool_args"].items()] + \
                [(f"content[{i}]", c) for i, c in content.items() if c.strip().startswith("{")]:
            try:
                json.loads(s)
                out[f"json_valid:{name}"] = True
            except json.JSONDecodeError as e:
                out[f"json_valid:{name}"] = f"INVALID: {e}"
    else:
        try:
            d = json.loads(text)
            out["json_body"] = d
            for ch in d.get("choices") or []:
                msg = ch.get("message") or {}
                c = msg.get("content")
                if isinstance(c, str) and c.strip().startswith("{"):
                    try:
                        json.loads(c)
                        out[f"json_valid:choices[{ch.get('index')}].content"] = True
                    except json.JSONDecodeError as e:
                        out[f"json_valid:choices[{ch.get('index')}].content"] = f"INVALID: {e}"
                for tc in msg.get("tool_calls") or []:
                    try:
                        json.loads(tc["function"]["arguments"])
                        out["json_valid:tool_args"] = True
                    except json.JSONDecodeError as e:
                        out["json_valid:tool_args"] = f"INVALID: {e}"
        except json.JSONDecodeError:
            out["json_body"] = text[:500]
    return out


def sdk_call(scen: str, stream: bool, extra: dict | None = None) -> dict:
    cl = openai.OpenAI(base_url=f"{BASE}/v1", api_key=KEY_A, max_retries=0,
                       default_headers={"x-synth-edge": scen})
    kw = dict(model="gpt-4o-mini", messages=[{"role": "user", "content": "hello there"}])
    kw.update(extra or {})
    try:
        if stream:
            acc = ""
            n = 0
            with cl.chat.completions.stream(**kw) as s:
                for ev in s:
                    n += 1
                final = s.get_final_completion()
            ch = final.choices[0]
            res = {"ok": True, "events": n, "content": ch.message.content,
                   "tool_calls": [t.function.arguments for t in (ch.message.tool_calls or [])],
                   "parsed": str(getattr(ch.message, "parsed", None))}
        else:
            r = cl.chat.completions.create(**kw)
            ch = r.choices[0]
            res = {"ok": True, "content": ch.message.content,
                   "tool_calls": [t.function.arguments for t in (ch.message.tool_calls or [])]}
    except Exception as e:  # noqa: BLE001 - the probe records whatever the SDK raises
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    return {"scenario": scen, "stream": stream, "sdk": res}


if __name__ == "__main__":
    out_path = sys.argv[1]
    results = []
    tools = [{"type": "function", "function": {"name": "send_mail", "parameters": {
        "type": "object", "properties": {"to": {"type": "string"}, "key": {"type": "string"},
                                         "n": {"type": "integer"}}}}}]
    plan = [
        ("logprobs-secret", True, {"logprobs": True, "top_logprobs": 1}),
        ("logprobs-secret", False, {"logprobs": True, "top_logprobs": 1}),
        ("refusal-secret", True, None), ("refusal-secret", False, None),
        ("reasoning-secret", True, None), ("reasoning-secret", False, None),
        ("fncall-secret", True, {"functions": [{"name": "send", "parameters": {"type": "object"}}]}),
        ("fncall-secret", False, {"functions": [{"name": "send", "parameters": {"type": "object"}}]}),
        ("tool-secret-split", True, {"tools": tools}), ("tool-secret", False, {"tools": tools}),
        ("n2-secret", True, {"n": 2}), ("n2-secret", False, {"n": 2}),
        ("json-numeric-pii", True, {"response_format": {"type": "json_object"}}),
        ("json-numeric-pii", False, {"response_format": {"type": "json_object"}}),
        ("zw-secret", True, None), ("zw-secret", False, None),
        ("spaced-secret", True, None),
        ("surrogate-split", True, None),
        ("provider-error-frame", True, None),
        ("status-429", False, None), ("status-429", True, None),
        ("", True, {"stream_options": {"include_usage": True}}),
    ]
    for scen, stream, extra in plan:
        r = raw_call(scen, stream, extra)
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "json_body"}, ensure_ascii=False)[:1500])
    # SDK acceptance for the shapes an application would parse
    sdk_plan = [
        ("tool-secret-split", True, {"tools": tools}),
        ("json-numeric-pii", True, {"response_format": {"type": "json_object"}}),
        ("json-numeric-pii", False, {"response_format": {"type": "json_object"}}),
        ("surrogate-split", True, {}),
        ("n2-secret", True, {"n": 2}),
        ("status-429", False, {}),
        ("", True, {"stream_options": {"include_usage": True}}),
    ]
    for scen, stream, extra in sdk_plan:
        r = sdk_call(scen, stream, extra)
        results.append(r)
        print(json.dumps(r, ensure_ascii=False)[:800])
    json.dump(results, open(out_path, "w"), ensure_ascii=False, indent=1, default=str)

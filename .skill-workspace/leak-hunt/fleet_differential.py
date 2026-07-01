"""E9 fleet differential (decision-layer). Drives each connected OpenRouter model
with routing DISABLED (enable_routing=false) so the pin is honored, then reads the
gateway's own pipeline_trace.prompt_out (post-redaction forwarded text) + zeroshield
action per model. Primary oracle = deterministic raw-token-intersection (catches the
bare-phone blind spot that aidefence_has_pii misses). Reads the temp key from /tmp.

NOTE: prompt_out is the gateway's TRACE of the forwarded payload, not the wire bytes
(Invariant II byte-proof still needs the MITM capture). This is the model-DIFFERENTIAL:
is redaction identical across every model for the same input?
"""
import json, re, urllib.request, urllib.error

KEY = open("/tmp/leakhunt_e9_key").read().strip()
GW = "http://127.0.0.1:8300/v1/chat/completions"
MODELS = ["gemma-free", "gpt4o-mini", "haiku-cheap"]  # real, allowlisted names
FIX = [
    {"id": "phone_email", "text": "call me at 8929554991 and email evance.maps@mail.com asap",
     "pii": ["8929554991", "evance.maps@mail.com"]},
    {"id": "ssn", "text": "my ssn is 123-45-6789 please update the record", "pii": ["123-45-6789"]},
    {"id": "cc", "text": "charge my card 4111 1111 1111 1111 today", "pii": ["4111 1111 1111 1111", "4111111111111111"]},
    {"id": "cred", "text": "deploy with key sk-proj-TESTabc123DEF456ghi and AKIAIOSFODNN7EXAMPLE",
     "pii": ["sk-proj-TESTabc123DEF456ghi", "AKIAIOSFODNN7EXAMPLE"]},
]


def call(model, text):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": text}],
                       "enable_routing": False, "max_tokens": 8}).encode()
    req = urllib.request.Request(GW, body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=90)
        return r.getcode(), json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as exc:
        return 0, {"_err": str(exc)}


def norm(s):
    return re.sub(r"\s+", "", s or "")


matrix = []
for fx in FIX:
    for m in MODELS:
        code, d = call(m, fx["text"])
        zs = d.get("zeroshield", {}) or {}
        pt = d.get("pipeline_trace", {}) or {}
        prompt_out = ""
        for st in pt.get("stages", []):
            if st.get("prompt_out"):
                prompt_out = st["prompt_out"]
        sel = (zs.get("routing") or {}).get("selected_model")
        leaked = [t for t in fx["pii"] if t in prompt_out or norm(t) in norm(prompt_out)]
        row = {"fixture": fx["id"], "model": m, "http": code, "action": zs.get("action"),
               "selected_model": sel, "prompt_out": prompt_out, "leaked": leaked}
        matrix.append(row)
        print(f'{fx["id"]:12s} {m:15s} http={code} action={str(zs.get("action")):7s} sel={str(sel):12s} leaked={leaked}')

json.dump(matrix, open("/tmp/e9_matrix.json", "w"))
print("\nWROTE /tmp/e9_matrix.json (" + str(len(matrix)) + " rows)")

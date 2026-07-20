# Live verification of the 3 streaming fixes against the gateway (real SSE).
import requests, json, re, time
from django.contrib.auth import get_user_model
from auth.models import Organization
from core.models import GatewayAPIKey, FirewallConfig
U=get_user_model(); GW="http://gateway:8300"
org=Organization.objects.get(slug="zeroshield"); owner=U.objects.filter(is_superuser=True).first()
# enable output PII guard (redact) so streaming PII is caught
cfg=FirewallConfig.load(organization=org)
for f,v in [("output_guardrails_enabled",True),("output_scanning_enabled",True),("pii_detection_enabled",True),("output_pii_action","redact")]:
    if hasattr(cfg,f): setattr(cfg,f,v)
cfg.save(); time.sleep(2)
inst,gk=GatewayAPIKey.generate_key(name="strm", owner=owner, project_id="zeroshield"); inst.organization_id=org.id; inst.save(update_fields=["organization"])
H={"Authorization":f"Bearer {gk}","Content-Type":"application/json"}
res={}
def rec(n,ok,d=""): res[n]=ok; print(f"{'PASS' if ok else 'FAIL'} [{n}] {d}")

def stream(body):
    chunks=[]
    r=requests.post(GW+"/v1/chat/completions", json={**body,"stream":True}, headers=H, timeout=90, stream=True)
    for raw in r.iter_lines():
        if raw:
            chunks.append(raw.decode("utf-8","replace"))
    return r.status_code, chunks

# ---- #1: terminal zeroshield trace frame before [DONE] (clean prompt) ----
try:
    code,chunks=stream({"model":"gemma-free","messages":[{"role":"user","content":"Say hello in 5 words."}],"max_tokens":40})
    joined="\n".join(chunks)
    # find the trace frame: a data chunk carrying "zeroshield" with action/request_id, before [DONE]
    done_idx=next((i for i,c in enumerate(chunks) if "[DONE]" in c), len(chunks))
    pre_done="\n".join(chunks[:done_idx])
    has_trace=("zeroshield" in pre_done.lower()) and ("action" in pre_done.lower() or "request_id" in pre_done.lower())
    rec("#1 terminal-trace-frame", code==200 and has_trace, f"code={code} trace_before_DONE={has_trace}")
except Exception as e: rec("#1 terminal-trace-frame", False, repr(e))

# ---- #2: PII must NOT appear raw in streamed content (redacted or held, never leaked) ----
try:
    code,chunks=stream({"model":"gemma-free","messages":[{"role":"user","content":"Output exactly this line and nothing else: Reach Alex Carter at alex.carter@example.com or 555-867-5309 anytime."}],"max_tokens":80})
    # extract streamed content deltas
    content=""
    for c in chunks:
        s=c.strip()
        if s.startswith("data: ") and "[DONE]" not in s:
            try:
                d=json.loads(s[6:]); ch=d.get("choices",[{}])
                if ch: content += (ch[0].get("delta",{}) or {}).get("content","") or ""
            except Exception: pass
    raw_email = bool(re.search(r'alex\.carter@example\.com', content))
    raw_phone = "555-867-5309" in content
    rec("#2 no-mid-stream-PII", code==200 and not raw_email and not raw_phone, f"code={code} raw_email_leaked={raw_email} raw_phone_leaked={raw_phone} | content[:120]={content[:120]!r}")
except Exception as e: rec("#2 no-mid-stream-PII", False, repr(e))

# ---- #3: streaming error frame must be sanitized (no traceback/path/model/fallback) ----
try:
    code,chunks=stream({"model":"definitely-not-a-real-model-xyz","messages":[{"role":"user","content":"hi"}],"max_tokens":10})
    joined="\n".join(chunks).lower()
    leaks = any(s in joined for s in ["traceback","/app/","/usr/lib","claude-haiku","gpt-oss","120b","fallback_group","litellm.","file \""])
    rec("#3 error-frame-sanitized", not leaks, f"code={code} leak={leaks}")
except Exception as e: rec("#3 error-frame-sanitized", False, repr(e))

inst.delete()
p=sum(1 for v in res.values() if v); print(f"\n==== STREAMING PASS={p} FAIL={len(res)-p} ===="); print("FAILED:", [k for k,v in res.items() if not v])

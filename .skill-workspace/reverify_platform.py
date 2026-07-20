# Re-verify the platform-naming/BYOK/isolation fixes against the live (rebuilt) stack.
import requests, json, time, uuid
from django.contrib.auth import get_user_model
from auth.models import Organization
from core.models import GatewayAPIKey
from policy.vector_models import VectorCollectionPolicy
from policy.vector_compiler import VectorPolicyCompiler
U=get_user_model(); GW="http://gateway:8300"; CTL="http://localhost:8000"
org=Organization.objects.get(slug="zeroshield"); owner=U.objects.filter(is_superuser=True).first()
inst,gk=GatewayAPIKey.generate_key(name="rpv", owner=owner, project_id="zeroshield"); inst.organization_id=org.id; inst.save(update_fields=["organization"])
GH={"Authorization":f"Bearer {gk}","Content-Type":"application/json"}
res={}
def rec(n,ok,d=""): res[n]=ok; print(f"{'PASS' if ok else 'FAIL'} [{n}] {d}")

# R3: bedrock/-prefixed reserved id rejected generically (no leak)
r=requests.post(GW+"/v1/chat/completions", json={"model":"bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0","messages":[{"role":"user","content":"hi"}],"max_tokens":5}, headers=GH, timeout=30)
b=r.text.lower()
rec("R3 bedrock-prefix-reserved", r.status_code==403 and "haiku" not in b and "bedrock" not in b and "120b" not in b, f"-> {r.status_code} leak={('haiku' in b or 'bedrock' in b or '120b' in b)}")

# R2: platform model not in /v1/models + not served (front door)
r2=requests.get(GW+"/v1/models", headers=GH, timeout=15)
ids=[m.get("id") for m in r2.json().get("data",[])] if r2.status_code==200 else []
rec("R2 platform-not-listed", not any("zeroshield-model" in str(i).lower() or "haiku" in str(i).lower() or "120b" in str(i).lower() for i in ids), f"ids={ids}")

# R6: embeddings nested/mixed input -> 400 fast (no DoS)
t0=time.time()
r6=requests.post(GW+"/v1/embeddings", json={"model":"zs-embed","input":[["nested"],{"d":1},5]}, headers=GH, timeout=20)
dt=time.time()-t0
rec("R6 embed-bad-input-400", r6.status_code==400 and dt<10, f"-> {r6.status_code} in {dt:.1f}s")

# R10: RAG collection case-variant evades deny? Create deny policy for 'docs', query 'Docs' -> must be denied
vp=VectorCollectionPolicy.objects.create(organization=org, project_id="zeroshield", collection_name="rdocs", vector_db_type="custom", default_action="deny", enabled=True, allowed_operations=["query"])
VectorPolicyCompiler().compile_and_push(trigger="rpv"); time.sleep(2)
codes={}
for variant in ["rdocs","RDOCS","Rdocs"]:
    rr=requests.post(GW+"/v1/rag/query", json={"query":"x","collection":variant,"vector_db_type":"chroma","top_k":3}, headers=GH, timeout=30)
    codes[variant]=rr.status_code
# deny+["query"] allows query; so to test case-variant we need a deny on a NON-allowed op OR a block. Use a fully-deny (no query in allowed)
vp.allowed_operations=[]; vp.save(); VectorPolicyCompiler().compile_and_push(trigger="rpv2"); time.sleep(2)
codes2={}
for variant in ["rdocs","RDOCS"]:
    rr=requests.post(GW+"/v1/rag/query", json={"query":"x","collection":variant,"vector_db_type":"chroma","top_k":3}, headers=GH, timeout=30)
    codes2[variant]=rr.status_code
rec("R10 case-variant-denied", codes2.get("RDOCS")==403 and codes2.get("rdocs")==403, f"deny-all query: {codes2} (both want 403 — case-variant can't evade)")
vp.delete(); VectorPolicyCompiler().compile_and_push(trigger="rpv-cleanup")

# R14: non-dict JSON body -> 400 not 500 (model isolate endpoint)
# use an org-admin JWT
jwt=requests.post(CTL+"/api/auth/token/", json={"email":"admin@zeroshield.io","password":"ZeroAdmin@2026"}, timeout=10)
AJ={"Authorization":f"Bearer {jwt.json().get('access')}"} if jwt.status_code==200 else {}
r14=requests.post(CTL+"/api/models/isolate/", json=["not","a","dict"], headers=AJ, timeout=10)
rec("R14 nondict-body-no-500", r14.status_code!=500, f"isolate non-dict body -> {r14.status_code} (want not 500)")

inst.delete()
p=sum(1 for v in res.values() if v); print(f"\n==== REVERIFY-PLATFORM PASS={p} FAIL={len(res)-p} ===="); print("FAILED:", [k for k,v in res.items() if not v])

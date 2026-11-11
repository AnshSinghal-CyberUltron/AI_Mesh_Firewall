"""L27-1 (effect on v2 traffic) + L27-2 (burst 5 -> 429 + Retry-After) against the REAL stack."""
import asyncio, json, time, httpx
C="http://127.0.0.1:38100"; G="http://127.0.0.1:38301"; K={"authorization":"Bearer dev-key-change-me"}
tok=httpx.post(f"{C}/api/auth/token/",json={"email":"admin@zeroshield.io","password":"Adm1n!Pass#2024"}).json()["access"]
H={"authorization":f"Bearer {tok}"}
out=[]
def put(d):
    r=httpx.put(f"{C}/api/firewall/config/",headers=H,json=d,timeout=30); out.append({"req":{"PUT":d},"status":r.status_code,"body":r.text[:300]}); time.sleep(3); return r
def chat(text,model="echo-1"):
    r=httpx.post(f"{G}/v1/chat/completions",headers=K,json={"model":model,"messages":[{"role":"user","content":text}]},timeout=30)
    rec={"req":{"POST":"/v1/chat/completions","prompt":text},"status":r.status_code,"headers":{k:v for k,v in r.headers.items() if k.lower() in("retry-after","x-should-retry","x-zeroshield-action")},"body":r.text[:300]}
    out.append(rec); return r
put({"requests_per_minute":1000,"burst_limit":150,"blocked_keywords":"password, secret, api_key, token","rate_limit_enabled":True})
# keyword effect
r=chat("my password is hunter2"); print("keyword blocked:",r.status_code)
put({"blocked_keywords":"zebrafoo"})
r=chat("my password is hunter2"); print("old keyword now allowed:",r.status_code)
r=chat("a zebrafoo please"); print("new keyword:",r.status_code)
put({"blocked_keywords":""})
# L27-2
put({"requests_per_minute":6000,"burst_limit":5}); time.sleep(3)
async def burst():
    async with httpx.AsyncClient(timeout=30) as c:
        rs=await asyncio.gather(*[c.post(f"{G}/v1/chat/completions",headers=K,json={"model":"echo-1","messages":[{"role":"user","content":f"burst {i}"}]}) for i in range(6)])
    return rs
rs=asyncio.run(burst())
codes=[r.status_code for r in rs]
for i,r in enumerate(rs): out.append({"burst_req":i+1,"status":r.status_code,"retry-after":r.headers.get("retry-after"),"x-should-retry":r.headers.get("x-should-retry"),"body":r.text[:200]})
print("L27-2 codes:",codes,[r.headers.get("retry-after") for r in rs])
put({"requests_per_minute":1000,"burst_limit":150})
json.dump(out,open("docs/plans/evidence/2026-11-04-integration/l27-effects.json","w"),indent=1)

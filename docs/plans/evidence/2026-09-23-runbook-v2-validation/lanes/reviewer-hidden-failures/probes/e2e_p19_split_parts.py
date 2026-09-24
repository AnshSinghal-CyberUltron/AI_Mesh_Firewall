"""E2E P19: a secret / PII value split across two `text` content parts of ONE message (the provider joins parts
with no separator, so the model reads the whole value).  rvproto scans each part as its own segment.
Same defect class the ledger records for v1 ('SSN split across parts ... egress raw')."""
import json, sys, time, uuid
import httpx
LOG = sys.argv[1]
can = {c["id"]: c["value"] for c in json.load(open(sys.argv[2]))["canaries"]}
KEY, MAIL, SSN = can["secret.aws"], can["pii.email"], can["pii.ssn"]
cases = {"AWS key whole (control)": [f"key {KEY} ok"],
         "AWS key split over 2 parts": [f"key {KEY[:9]}", f"{KEY[9:]} ok"],
         "email split over 2 parts": [f"mail {MAIL[:6]}", f"{MAIL[6:]} ok"],
         "SSN split over 2 parts": [f"ssn {SSN[:5]}", f"{SSN[5:]} ok"]}
res = {}
with httpx.Client(base_url="http://127.0.0.1:47400", timeout=30) as c:
    for name, parts in cases.items():
        rid = "p19-" + uuid.uuid4().hex[:10]
        doc = {"model": "gpt-4o-mini", "max_tokens": 3, "messages": [{"role": "user", "content": [{"type": "text", "text": t} for t in parts]}]}
        r = c.post("/v1/chat/completions", json=doc, headers={"authorization": "Bearer sk-rv-org-a-0001", "x-request-id": rid})
        res[name] = (rid, r.status_code, r.headers.get("x-rv-disposition"))
time.sleep(1.5)
prov = {json.loads(l)["rid"]: json.loads(l) for l in open(f"{LOG}/prov-records.jsonl")}
for name, (rid, st, disp) in res.items():
    p = prov.get(rid)
    # synthprov canary hits look at raw body + parsed text; a split value is not a verbatim hit, so check
    # what the provider's model would read: the parts concatenated
    print(f"{name:<28} http={st} disposition={disp:<6} provider called={p is not None} "
          f"provider canary_hits={p.get('canary_hits') if p else '-'}")

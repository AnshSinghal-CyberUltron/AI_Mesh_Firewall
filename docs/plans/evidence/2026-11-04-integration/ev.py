"""Tiny recorder: every call is a REAL HTTP request; request, status and body excerpt are appended to a jsonl file."""
import json, time, httpx
G = "http://127.0.0.1:38301"; C = "http://127.0.0.1:38100"
KEY = "dev-key-change-me"
def control_token():
    return httpx.post(f"{C}/api/auth/token/", json={"email": "admin@zeroshield.io", "password": "Adm1n!Pass#2024"}).json()["access"]
class Rec:
    def __init__(self, name): self.path = f"docs/plans/evidence/2026-11-04-integration/{name}.jsonl"; open(self.path, "w").close()
    def call(self, label, method, url, body=None, headers=None, key=KEY, excerpt=600, raw=None):
        h = {"authorization": f"Bearer {key}"} if key else {}
        h.update(headers or {})
        t = time.time()
        r = httpx.request(method, url, headers=h, json=body, content=raw, timeout=60)
        rec = {"label": label, "request": {"method": method, "url": url, "body": body if raw is None else "<raw>"},
               "status": r.status_code, "resp_headers": {k: v for k, v in r.headers.items() if k.lower() in ("retry-after", "x-should-retry", "x-zeroshield-action", "x-request-id", "content-type", "www-authenticate")},
               "body_excerpt": r.text[:excerpt], "ms": round((time.time() - t) * 1000)}
        open(self.path, "a").write(json.dumps(rec) + "\n")
        return r
def chat(rec, label, text, **kw):
    return rec.call(label, "POST", f"{G}/v1/chat/completions", {"model": "echo-1", "messages": [{"role": "user", "content": text}], **kw})

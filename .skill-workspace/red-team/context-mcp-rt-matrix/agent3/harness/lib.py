import httpx, json, os, re, base64, hashlib, time

BASE = os.environ["GATEWAY_BASE_URL"]
KEY = os.environ["GATEWAY_API_KEY"]
ORG = "zeroshield"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
EVID_DIR = ".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence"

CANARY = f"RT3-{hashlib.sha1(os.urandom(8)).hexdigest()[:10]}"

PII_PATTERNS = {
    "ssn": r"123-45-6789",
    "aws_key": r"AKIAIOSFODNN7EXAMPLE",
    "email": r"redteam3\.[a-z]*@corp\.example",
    "email_generic": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "cc": r"4111[- ]?1111[- ]?1111[- ]?1111",
    "openai_key": r"sk-[a-zA-Z0-9]{20,}",
    "github_pat": r"ghp_[a-zA-Z0-9]{20,}",
}

_id_counter = [0]

def _next_id():
    _id_counter[0] += 1
    return _id_counter[0]

def redact_headers(h):
    hh = dict(h)
    if "Authorization" in hh:
        v = hh["Authorization"]
        hh["Authorization"] = v[:14] + "...<redacted>...cti863GadI1I2" if False else "Bearer <redacted>"
    return hh

def call_tool(server, name, arguments, note="", timeout=60):
    url = f"{BASE}/gateway/{ORG}/mcp/{server}"
    body = {"jsonrpc": "2.0", "id": _next_id(), "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    t0 = time.time()
    try:
        r = httpx.post(url, headers=HEADERS, json=body, timeout=timeout)
        dt = time.time() - t0
        try:
            j = r.json()
        except Exception:
            j = {"_non_json_body": r.text[:2000]}
        rec = {"server": server, "tool": name, "note": note, "request": {"url": url, "headers": redact_headers(HEADERS), "body": body}, "response": {"http_status": r.status_code, "body": j}, "elapsed_s": round(dt, 3)}
    except Exception as e:
        rec = {"server": server, "tool": name, "note": note, "request": {"url": url, "body": body}, "response": {"error": str(e)}, "elapsed_s": round(time.time()-t0,3)}
    return rec

def list_tools(server):
    url = f"{BASE}/gateway/{ORG}/mcp/{server}"
    body = {"jsonrpc": "2.0", "id": _next_id(), "method": "tools/list", "params": {}}
    r = httpx.post(url, headers=HEADERS, json=body, timeout=30)
    return r.json()

def scan_pii(obj):
    """Scan an arbitrary JSON-serializable object's full text for raw PII/secret patterns."""
    text = json.dumps(obj, default=str)
    hits = {}
    for k, pat in PII_PATTERNS.items():
        found = re.findall(pat, text)
        if found:
            hits[k] = found[:5]
    return hits

def save_evidence(rec, filename):
    path = os.path.join(EVID_DIR, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(rec, f, indent=2, default=str)
    return path

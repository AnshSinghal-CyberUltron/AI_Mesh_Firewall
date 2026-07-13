#!/usr/bin/env python3
"""Agent4 discovery pass: tools/list against every known server slug, save raw schemas."""
import httpx, os, json, sys

KEY = os.environ["GATEWAY_API_KEY"]
BASE = os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8300")
ORG = "zeroshield"
OUT = os.path.join(os.path.dirname(__file__), "..", "evidence", "_discovery")
os.makedirs(OUT, exist_ok=True)

SERVERS = [
    "semgrep-mcp", "playwright-mcp", "cp09-ens8do", "cp09-verify", "playwright",
    "everything-1", "everything-2", "everything-3", "everything-4", "everything-5",
    "filesystem-canary", "everything-mcp",
    "ws-everything-stub", "sse-everything-stub", "http-everything-stub", "linear-manual-oauth",
]

HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def rpc(server, method, params=None, id_=1):
    url = f"{BASE}/gateway/{ORG}/mcp/{server}"
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    try:
        r = httpx.post(url, headers=HEADERS, json=body, timeout=60)
        try:
            j = r.json()
        except Exception:
            j = {"_raw_text": r.text[:2000]}
        return {"http_status": r.status_code, "body": j}
    except Exception as e:
        return {"http_status": None, "error": str(e)}


def redact_headers(h):
    hh = dict(h)
    if "Authorization" in hh:
        k = KEY
        hh["Authorization"] = f"Bearer {k[:4]}...{k[-4:]}"
    return hh


summary = {}
for s in SERVERS:
    res = rpc(s, "tools/list")
    fname = os.path.join(OUT, f"tools_list_{s}.json")
    with open(fname, "w") as f:
        json.dump({
            "server": s,
            "url": f"{BASE}/gateway/{ORG}/mcp/{s}",
            "request_headers": redact_headers(HEADERS),
            "response": res,
        }, f, indent=2, default=str)
    tools = []
    try:
        tools = res["body"]["result"]["tools"]
        names = [t["name"] for t in tools]
    except Exception:
        names = []
    summary[s] = {
        "http_status": res.get("http_status"),
        "tool_names": names,
        "error": res.get("body", {}).get("error") if isinstance(res.get("body"), dict) else None,
    }
    print(s, "->", res.get("http_status"), names or res.get("body"))

with open(os.path.join(OUT, "_summary.json"), "w") as f:
    json.dump(summary, f, indent=2, default=str)
print("\nDone. Summary written to", os.path.join(OUT, "_summary.json"))

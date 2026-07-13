import httpx, json, os, sys

BASE = os.environ["GATEWAY_BASE_URL"]
KEY = os.environ["GATEWAY_API_KEY"]
ORG = "zeroshield"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

SLUGS = [
    "semgrep-mcp", "playwright-mcp", "cp09-ens8do", "cp09-verify", "playwright",
    "everything-1", "everything-2", "everything-3", "everything-4", "everything-5",
    "filesystem-canary", "everything-mcp",
    "ws-everything-stub", "sse-everything-stub", "http-everything-stub",
    "linear-manual-oauth",
]

results = {}
for slug in SLUGS:
    url = f"{BASE}/gateway/{ORG}/mcp/{slug}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    try:
        r = httpx.post(url, headers=HEADERS, json=body, timeout=30)
        j = r.json()
        tools = j.get("result", {}).get("tools", [])
        err = j.get("error")
        results[slug] = {"http": r.status_code, "n_tools": len(tools), "tool_names": [t.get("name") for t in tools], "error": err}
    except Exception as e:
        results[slug] = {"http": None, "error": str(e)}
    print(slug, "->", results[slug].get("n_tools"), results[slug].get("error"))

with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_discover.json", "w") as f:
    json.dump(results, f, indent=2)

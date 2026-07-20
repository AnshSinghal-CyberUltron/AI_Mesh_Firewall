import httpx, json, os

BASE = os.environ["GATEWAY_BASE_URL"]
KEY = os.environ["GATEWAY_API_KEY"]
ORG = "zeroshield"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

url = f"{BASE}/gateway/{ORG}/mcp/everything-mcp"
body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
r = httpx.post(url, headers=HEADERS, json=body, timeout=30)
j = r.json()
tools = j.get("result", {}).get("tools", [])
with open(".skill-workspace/red-team/context-mcp-rt-matrix/agent3/evidence/_everything_mcp_tools_full.json", "w") as f:
    json.dump(tools, f, indent=2)
for t in tools:
    print("###", t.get("name"))
    print(json.dumps(t.get("inputSchema", {}), indent=2)[:800])
    print()

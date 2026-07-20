# ZeroShield · OpenAI SDK Reference Demo

Canonical demo app for live-verifying AI Mesh Firewall through the **stock OpenAI Python SDK** only.

Frontend is a static product UI (OKLCH light/dark, Plus Jakarta Sans + JetBrains Mono) served by FastAPI — rebuilt for ZeroShield console parity.

```text
Browser  →  /demo/ (demo FastAPI)  →  openai.OpenAI(base_url=gateway/v1, api_key=org_key)
Login     →  control POST /api/auth/token/ + org simulator key (same credentials as console)
```

## URLs

| Environment | URL |
|-------------|-----|
| Local (Vite proxy) | http://localhost:8180/demo/ or http://127.0.0.1:8180/demo/ |
| Local (direct) | http://127.0.0.1:8770/ |
| Production | https://aimeshfirewall.zeroshield.ai/demo/ |

Bare `/demo` redirects to `/demo/` (Vite + nginx) so CSS/JS and login stay under the `/demo` prefix. Both `localhost` and `127.0.0.1` work once port **8180** is reachable (on a remote SSH box, forward 8180 from your laptop).

Sign in with your console email/password. The demo binds to **your organization** (models, policies, simulator gateway key).

Gate: `node scripts/ralph/demo_localhost_path_verify.mjs`

## Run locally (compose)

```bash
make up-demo
# or full stack: make up
# open http://localhost:8180/demo/  (or 127.0.0.1 — both work)
```

## Run locally (standalone)

```bash
cd examples/zeroshield-openai-demo/backend
pip install -r requirements.txt
export CONTROL_BASE_URL=http://127.0.0.1:8100
export ZEROSHIELD_BASE_URL=http://127.0.0.1:8300/v1
uvicorn main:app --port 8770
```

## SDK-only guarantee

- Every AI / RAG / observability call uses `openai.OpenAI`
- No Anthropic / Bedrock / Google provider SDKs
- Control plane is used for login + org gateway-key provisioning; the demo also
  proxies MCP **server/tool catalog + tools/call** through control so the UI can
  pick a registered server (keys never leave the demo server)

## MCP: context vs tools

The **MCP Context** tab injects `extra_body.mcp_context` into chat (firewall
scans structured CRM JSON). That is **not** MCP protocol tool selection.

To pick a **specific MCP server and tool**, use the same tab’s **Tool execution**
section, the main console Guardrail Simulator, or the gateway JSON-RPC URL
`/gateway/{org}/mcp/{server}`. Full patterns: [docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md).

Gateway MCP client sample (no OpenAI SDK):

```bash
export ZEROSHIELD_API_KEY=... ZEROSHIELD_ORG_SLUG=zeroshield MCP_SERVER_SLUG=everything-1
python mcp_gateway_client.py
```

## Note on `demo/zeroshield-openai-demo/`

That tree is legacy. Prefer **this** directory (`examples/zeroshield-openai-demo`) — it is what ECR `ai-mesh-demo` and local compose build.

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

## What the firewall did — and did not do

The demo reports governance faithfully, including when the answer is "nothing was
enforced". Two surfaces exist for this:

**Governance signals strip** — the gateway sends some verdicts as *response headers*
rather than body fields. The demo reads them off the stock SDK's
`.with_raw_response` accessor (same call, no extra request) and renders only what
actually arrived:

| Header | Meaning | When it appears |
|---|---|---|
| `x-ratelimit-limit-tokens` / `-remaining-tokens` / `-reset-tokens` | token quota | only when the key carries a non-zero ceiling |
| `x-ratelimit-limit-requests` / `-remaining-requests` / `-reset-requests` | request quota | only when an RPM ceiling is set |
| `x-zeroshield-clamped` | params the gateway silently rewrote, e.g. `n=5->1,max_tokens=9999->4096` | only when a clamp fired |
| `x-zeroshield-review-required` | operator configured `human_review`; verdict normalises to `flag` | only when such a detector fired |

An empty strip means *the gateway sent no such header* — never "all clear".

**Refusal kind.** Not every non-2xx is a security verdict. The demo separates them by
the OpenAI error code, because `context_length_exceeded` and `content_filter` are both
HTTP 400 with the same `"Request blocked due to security policy"` message:

| `error.code` | shown as | category |
|---|---|---|
| `content_filter` | blocked by security policy | `policy_violation` |
| `context_length_exceeded` | refused on **size**, not content | `dos` |
| `rate_limit_exceeded` | refused on **quota**, not content | — |

## MCP: context vs tools

The **MCP Context** tab injects `extra_body.mcp_context` into chat (firewall
scans structured CRM JSON). That is **not** MCP protocol tool selection.

### MCP scan posture is operator-selected — the demo shows which one is active

MCP enforcement is *strictly* operator-selected. `tag` (the **server default**) and
`monitor` DETECT and TAG findings but never modify or block; only `redact` and `block`
enforce. So a tool result marked `decision: allow` under a `tag` posture has **not**
been sanitized — nothing was going to be enforced either way.

The Tool execution panel states the org's live posture (from control's
`mcp_ext_scan_action`) and repeats it on every result, rather than letting a bare
`decision: allow` imply an approval. An unreadable or unset posture is reported as
observe-only — never assumed to be enforcing.

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

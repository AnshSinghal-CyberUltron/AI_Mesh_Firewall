"""Generate customer-facing OpenAI SDK Python snippets for the demo UI."""
from __future__ import annotations

import json
from typing import Any

from config import GATEWAY_BASE_URL


def _fmt(obj: Any) -> str:
    return json.dumps(obj, indent=2)


def chat_snippet(
    messages: list[dict],
    model: str = "auto",
    *,
    stream: bool = False,
    extra_body: dict | None = None,
    max_tokens: int = 512,
) -> str:
    extra = ""
    if extra_body:
        extra = f",\n    extra_body={_fmt(extra_body)}"
    if stream:
        return f'''from openai import OpenAI

client = OpenAI(
    api_key="<YOUR_ZEROSHIELD_GATEWAY_KEY>",
    base_url="{GATEWAY_BASE_URL}",
)

stream = client.chat.completions.create(
    model={model!r},
    messages={_fmt(messages)},
    max_tokens={max_tokens},
    stream=True{extra},
)
for chunk in stream:
    delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
    if delta:
        print(delta, end="", flush=True)
'''
    return f'''from openai import OpenAI

client = OpenAI(
    api_key="<YOUR_ZEROSHIELD_GATEWAY_KEY>",
    base_url="{GATEWAY_BASE_URL}",
)

resp = client.chat.completions.with_raw_response.create(
    model={model!r},
    messages={_fmt(messages)},
    max_tokens={max_tokens}{extra},
)
body = resp.parse()  # or json.loads(resp.text)
print(body.choices[0].message.content)
# ZeroShield extras on the same OpenAI-compatible body:
#   body.model_extra["zeroshield"], body.model_extra["pipeline_trace"]
'''


def route_snippet(input_text: str, model: str, prefs: dict) -> str:
    return chat_snippet(
        [{"role": "user", "content": input_text}],
        model=model,
        extra_body={"routing_preferences": prefs} if prefs else None,
        max_tokens=400,
    )


def mcp_snippet(
    input_text: str,
    context: dict,
    model: str = "auto",
    *,
    extra_body: dict | None = None,
) -> str:
    ctx_prose = ", and ".join(
        f"the {k.replace('_', ' ')} is {v}" for k, v in context.items()
    )
    messages = [
        {"role": "user", "content": f"{input_text} For this customer, {ctx_prose}."},
    ]
    body = dict(extra_body) if isinstance(extra_body, dict) else {"mcp_context": context}
    if "mcp_context" not in body:
        body["mcp_context"] = context
    return chat_snippet(
        messages,
        model=model,
        extra_body=body,
        max_tokens=500,
    )


def mcp_tool_call_snippet(
    server_slug: str,
    tool_name: str,
    arguments: dict | None = None,
    *,
    control_base_url: str | None = None,
) -> str:
    """Control-plane tools/call (Pattern B) — same API as the console simulator."""
    from config import CONTROL_BASE_URL

    base = (control_base_url or CONTROL_BASE_URL).rstrip("/")
    args = arguments if isinstance(arguments, dict) else {}
    return f'''import httpx

# Pattern B — pick server + tool via the control API (JWT from console login).
# The demo proxies this as POST /api/mcp/tools/call so the gateway key stays server-side.
CONTROL = {base!r}
TOKEN = "<CONTROL_JWT>"

resp = httpx.post(
    f"{{CONTROL}}/api/mcp-connector/tools/call/",
    headers={{"Authorization": f"Bearer {{TOKEN}}", "Content-Type": "application/json"}},
    json={{
        "server_slug": {server_slug!r},
        "name": {tool_name!r},
        "arguments": {_fmt(args)},
    }},
    timeout=60.0,
)
print(resp.status_code, resp.json())
'''


def mcp_jsonrpc_snippet(
    org_slug: str,
    server_slug: str,
    tool_name: str = "echo",
    arguments: dict | None = None,
    *,
    gateway_host: str | None = None,
) -> str:
    """Gateway JSON-RPC tools/call (Pattern C) — MCP Client / httpx shape."""
    host = (gateway_host or _gateway_host()).rstrip("/")
    args = arguments if isinstance(arguments, dict) else {"message": "hello"}
    return f'''import httpx

# Pattern C — official MCP Client shape over Streamable HTTP JSON-RPC.
# Point at /gateway/{{org}}/mcp/{{server}} — NOT at /v1 (that path is OpenAI chat only).
GATEWAY = {host!r}
ORG = {org_slug!r}
SERVER = {server_slug!r}
KEY = "<YOUR_ZEROSHIELD_GATEWAY_KEY>"
url = f"{{GATEWAY}}/gateway/{{ORG}}/mcp/{{SERVER}}"
headers = {{"Authorization": f"Bearer {{KEY}}", "Content-Type": "application/json"}}

def rpc(method, params=None, id_=1):
    body = {{"jsonrpc": "2.0", "id": id_, "method": method}}
    if params is not None:
        body["params"] = params
    return httpx.post(url, headers=headers, json=body, timeout=60.0).json()

rpc("initialize", {{
    "protocolVersion": "2024-11-05",
    "capabilities": {{}},
    "clientInfo": {{"name": "zeroshield-demo", "version": "1.0"}},
}})
print("tools:", rpc("tools/list", {{}}, id_=2))
print("call:", rpc("tools/call", {{
    "name": {tool_name!r},
    "arguments": {_fmt(args)},
}}, id_=3))
'''


def _gateway_host() -> str:
    """Strip trailing /v1 from the OpenAI base URL to get the gateway host."""
    base = GATEWAY_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3]
    return base


def rag_ingest_snippet(name: str, content: str) -> str:
    return f'''from openai import OpenAI
import httpx

client = OpenAI(
    api_key="<YOUR_ZEROSHIELD_GATEWAY_KEY>",
    base_url="{GATEWAY_BASE_URL}",
)

r = client.post(
    "/rag/ingest",
    cast_to=httpx.Response,
    body={{
        "collection": "zeroshield-rag-e2e",
        "documents": [{{"text": {content!r}, "metadata": {{"name": {name!r}}}}}],
    }},
)
print(r.status_code, r.json())
'''


def rag_query_snippet(
    query: str,
    model: str = "auto",
    *,
    extra_body: dict | None = None,
) -> str:
    extra = ""
    if extra_body:
        extra = f",\n    extra_body={_fmt(extra_body)}"
    return f'''from openai import OpenAI
import httpx

client = OpenAI(
    api_key="<YOUR_ZEROSHIELD_GATEWAY_KEY>",
    base_url="{GATEWAY_BASE_URL}",
)

# 1) Governed retrieval
hits = client.post(
    "/rag/query",
    cast_to=httpx.Response,
    body={{"collection": "zeroshield-rag-e2e", "query": {query!r}, "n_results": 3}},
).json()

# 2) Synthesize through firewalled chat
docs = hits.get("documents") or []
context = "\\n\\n".join(d.get("content") or d.get("text") or "" for d in docs)
resp = client.chat.completions.create(
    model={model!r},
    messages=[
        {{"role": "system", "content": "Answer ONLY from the provided context."}},
        {{"role": "user", "content": f"Context:\\n{{context}}\\n\\nQuestion: {query}"}},
    ]{extra},
)
print(resp.choices[0].message.content)
'''


def validate_snippet(
    input_text: str,
    model: str = "auto",
    *,
    extra_body: dict | None = None,
) -> str:
    return chat_snippet(
        [{"role": "user", "content": input_text}],
        model=model,
        extra_body=extra_body,
        max_tokens=400,
    )


def files_snippet(
    instruction: str,
    model: str = "auto",
    *,
    extra_body: dict | None = None,
) -> str:
    extra = ""
    if extra_body:
        extra = f",\n    extra_body={_fmt(extra_body)}"
    return f'''from openai import OpenAI

client = OpenAI(
    api_key="<YOUR_ZEROSHIELD_GATEWAY_KEY>",
    base_url="{GATEWAY_BASE_URL}",
)

# Extract text client-side (PDF/DOCX/TXT), then send through the firewall:
excerpt = open("document.txt").read()[:6000]
resp = client.chat.completions.create(
    model={model!r},
    messages=[
        {{"role": "system", "content": "You analyze documents. Be concise and factual."}},
        {{"role": "user", "content": {instruction!r} + "\\n\\nDocument:\\n" + excerpt}},
    ],
    max_tokens=2048{extra},
)
print(resp.choices[0].message.content)
'''

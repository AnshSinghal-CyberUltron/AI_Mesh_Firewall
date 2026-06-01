"""
Hermetic MCP stub server for Submodule 1.4 live verification.

Implements the streamable-http MCP protocol the gateway expects:
  POST /mcp  -> initialize | notifications/initialized | tools/list | tools/call

Exposes three deterministic tools designed to exercise 1.4:
  - echo(msg)                : returns msg verbatim (no PII; baseline)
  - get_user_record(user_id) : returns dict containing email + SSN-shaped value
                               (used to verify G7 field redaction & compliance
                                tagging on response values)
  - search_secret(query)     : returns a "secret" string ONLY when the request
                               carries Authorization: Bearer <token>, otherwise
                               returns an error. Used to verify Bearer auth is
                               actually forwarded by the gateway.

Deliberately stateless and offline (no external network) so verification is
deterministic and CI-safe. No backend `npx` invocation is involved; the
gateway treats this URL exactly like any public streamable-http MCP server.
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="ZeroShield MCP Stub", version="1.0.0")

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the input message.",
        "inputSchema": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    },
    {
        "name": "get_user_record",
        "description": "Return a user record containing email + ssn (PII test fixture).",
        "inputSchema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "search_secret",
        "description": "Return a confidential string. Requires Bearer auth.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def _ok(req_id, result):
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id, code, message):
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/mcp")
async def mcp(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}}, status_code=400)

    method = body.get("method") or ""
    req_id = body.get("id", 1)

    # 1. initialize handshake
    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "zeroshield-mcp-stub", "version": "1.0.0"},
            },
        )

    # 2. notifications/initialized — no body expected
    if method == "notifications/initialized":
        return JSONResponse({}, status_code=200)

    # 3. tools/list
    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})

    # 4. tools/call
    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name") or ""
        args = params.get("arguments") or {}

        if name == "echo":
            text = str(args.get("msg", ""))
            return _ok(req_id, {"content": [{"type": "text", "text": text}]})

        if name == "get_user_record":
            uid = str(args.get("user_id", "u-1"))
            # Intentional PII fixture: email + SSN-shaped value.
            payload = {
                "user_id": uid,
                "name": "Jane Doe",
                "email": "jane.doe@example.com",
                "ssn": "123-45-6789",
                "phone": "+1-555-0100",
                "internal_note": "VIP customer",
            }
            return _ok(
                req_id,
                {"content": [{"type": "text", "text": str(payload)}], "structuredContent": payload},
            )

        if name == "search_secret":
            authz = request.headers.get("authorization") or ""
            if not authz.lower().startswith("bearer "):
                return _err(req_id, -32001, "Bearer token required")
            return _ok(
                req_id,
                {"content": [{"type": "text", "text": "secret-value-42"}]},
            )

        return _err(req_id, -32601, f"unknown tool: {name}")

    return _err(req_id, -32601, f"unknown method: {method}")

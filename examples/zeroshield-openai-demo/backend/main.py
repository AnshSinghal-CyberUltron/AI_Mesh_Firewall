"""
ZeroShield OpenAI-SDK Demo — backend.

Login uses the same control credentials as the console. All AI / RAG / MCP /
observability traffic goes through stock ``openai.OpenAI(base_url, api_key)``
against the ZeroShield gateway for the logged-in org.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import auth as demo_auth
import sdk_snippets as snippets
import session_keys
import zeroshield_client as zs
from config import CONTROL_BASE_URL, GATEWAY_BASE_URL

app = FastAPI(title="ZeroShield OpenAI-SDK Demo")

_RAG: dict[str, dict] = {}


def _client_for(user: dict):
    try:
        key, _session = session_keys.gateway_key_for_user(user)
        return zs.make_client(api_key=key, base_url=GATEWAY_BASE_URL)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _with_snippet(payload: dict, snippet: str) -> dict:
    out = dict(payload)
    out["sdk_snippet"] = snippet
    out["gateway_base_url"] = GATEWAY_BASE_URL
    return out


def _routing_prefs_for_model(model: str, **extra: Any) -> dict[str, Any]:
    """Gateway requires enable_routing for literal model='auto' to resolve."""
    prefs: dict[str, Any] = dict(extra)
    m = str(model or "auto").strip()
    prefs["enable_routing"] = (not m or m == "auto")
    return prefs


def _ensure_routing_extra(model: str, extra_body: dict | None = None) -> dict[str, Any]:
    """Merge routing_preferences.enable_routing into any existing extra_body."""
    out: dict[str, Any] = dict(extra_body or {})
    existing = out.get("routing_preferences")
    base = dict(existing) if isinstance(existing, dict) else {}
    if "enable_routing" not in base:
        base["enable_routing"] = _routing_prefs_for_model(model)["enable_routing"]
    out["routing_preferences"] = base
    return out


# ───────────────────────── Auth ─────────────────────────
class LoginReq(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


@app.post("/api/login")
def login(req: LoginReq):
    result = demo_auth.authenticate(req.email, req.password)
    user = result["user"]
    user["_access_token"] = result["access"]
    session = session_keys.ensure_gateway_key(result["access"], user)
    return {
        "access": result["access"],
        "refresh": result["refresh"],
        "user": {
            "id": user.get("id"),
            "email": user.get("email"),
            "roles": user.get("roles") or [],
            "organization": user.get("organization"),
            "is_superuser": user.get("is_superuser"),
        },
        "org": session.get("org_slug"),
        "org_name": session.get("org_name"),
        "key_prefix": session.get("prefix"),
        "gateway_base_url": GATEWAY_BASE_URL,
        "control_base_url": CONTROL_BASE_URL,
    }


@app.post("/api/logout")
def logout(user: dict = Depends(demo_auth.require_user)):
    session_keys.clear_session(user.get("_access_token") or "")
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(demo_auth.require_user)):
    token = user.get("_access_token") or ""
    session = session_keys.ensure_gateway_key(token, user)
    return {
        "user": {
            "id": user.get("id"),
            "email": user.get("email"),
            "roles": user.get("roles") or [],
            "organization": user.get("organization"),
            "is_superuser": user.get("is_superuser"),
        },
        "org": session.get("org_slug"),
        "org_name": session.get("org_name"),
        "key_prefix": session.get("prefix"),
        "gateway_base_url": GATEWAY_BASE_URL,
    }


# ───────────────────────── Health / models ─────────────────────────
@app.get("/api/health")
def health():
    """Unauthenticated liveness (nginx / compose). Auth required for models."""
    return {
        "ok": True,
        "auth_required": True,
        "gateway_base_url": GATEWAY_BASE_URL,
        "control_base_url": CONTROL_BASE_URL,
    }


@app.get("/api/models")
def models(user: dict = Depends(demo_auth.require_user)):
    try:
        return {"models": zs.list_models(_client_for(user)), "gateway_base_url": GATEWAY_BASE_URL}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/observability")
def observability(user: dict = Depends(demo_auth.require_user)):
    return zs.observability(_client_for(user))


# ───────────────────────── Chat ─────────────────────────
class ChatReq(BaseModel):
    messages: list[dict]
    model: str = "auto"
    max_tokens: int = 512
    stream: bool = False
    extra_body: dict | None = None


@app.post("/api/chat")
def chat(req: ChatReq, user: dict = Depends(demo_auth.require_user)):
    extra = _ensure_routing_extra(req.model, req.extra_body)
    r = zs.chat(
        _client_for(user),
        req.messages,
        model=req.model,
        max_tokens=req.max_tokens,
        extra_body=extra,
    )
    snip = snippets.chat_snippet(
        req.messages,
        req.model,
        stream=False,
        extra_body=extra,
        max_tokens=req.max_tokens,
    )
    return _with_snippet(r, snip)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatReq, user: dict = Depends(demo_auth.require_user)):
    # Emit the SDK snippet immediately, then collect the sync OpenAI stream in a
    # worker thread. Running sync httpx streaming on the ASGI loop hangs under
    # model=auto; to_thread after the first SSE frame is reliable on this stack.
    import asyncio

    client = _client_for(user)
    extra = _ensure_routing_extra(req.model, req.extra_body)
    snip = snippets.chat_snippet(
        req.messages,
        req.model,
        stream=True,
        extra_body=extra,
        max_tokens=req.max_tokens,
    )

    def _collect() -> list[tuple[str, Any]]:
        return list(
            zs.chat_stream(
                client,
                req.messages,
                model=req.model,
                max_tokens=req.max_tokens,
                extra_body=extra,
            )
        )

    async def gen():
        yield f"data: {json.dumps({'sdk_snippet': snip})}\n\n"
        try:
            items = await asyncio.to_thread(_collect)
        except Exception as e:
            yield f"data: {json.dumps({'delta': f'\\n[stream error: {e}]'})}\n\n"
            items = []
        for kind, payload in items:
            if kind == "delta":
                yield f"data: {json.dumps({'delta': payload})}\n\n"
            elif kind == "trace":
                yield f"data: {json.dumps({'trace': payload})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ───────────────────────── Routing ─────────────────────────
class RouteReq(BaseModel):
    input: str
    model: str = "auto"
    risk_weight: float | None = None
    cost_weight: float | None = None
    latency_weight: float | None = None
    data_sensitivity: str | None = None


@app.post("/api/route")
def route(req: RouteReq, user: dict = Depends(demo_auth.require_user)):
    weight_prefs: dict[str, Any] = {}
    for k in ("risk_weight", "cost_weight", "latency_weight"):
        v = getattr(req, k)
        if v is not None:
            weight_prefs[k] = v
    if req.data_sensitivity:
        weight_prefs["data_sensitivity"] = req.data_sensitivity
    prefs = _routing_prefs_for_model(req.model, **weight_prefs)
    extra_body: dict[str, Any] = {"routing_preferences": prefs}
    r = zs.chat(
        _client_for(user),
        [{"role": "user", "content": req.input}],
        model=req.model,
        extra_body=extra_body,
        max_tokens=400,
    )
    return _with_snippet(
        {"output_text": r["content"], "model": r["model"], "trace": r["trace"]},
        snippets.route_snippet(req.input, req.model, prefs),
    )


# ───────────────────────── Validation ─────────────────────────
class ValidateReq(BaseModel):
    input: str
    model: str = "auto"


@app.post("/api/validate")
def validate(req: ValidateReq, user: dict = Depends(demo_auth.require_user)):
    extra = _ensure_routing_extra(req.model)
    r = zs.chat(
        _client_for(user),
        [{"role": "user", "content": req.input}],
        model=req.model,
        max_tokens=400,
        extra_body=extra,
    )
    return _with_snippet(r, snippets.validate_snippet(req.input, req.model, extra_body=extra))


# ───────────────────────── RAG ─────────────────────────
class RagIngestReq(BaseModel):
    name: str
    content: str


@app.post("/api/rag/ingest")
def rag_ingest(req: RagIngestReq, user: dict = Depends(demo_auth.require_user)):
    res = zs.rag_ingest(_client_for(user), req.name, req.content)
    if res.get("ok"):
        doc_id = re.sub(r"[^a-z0-9]+", "-", req.name.lower())[:40] or "doc"
        chunks = res.get("ingested_count") or 1
        _RAG[doc_id] = {"name": req.name, "chars": len(req.content), "chunks": chunks}
        res.setdefault("chunks", chunks)
        res.setdefault("name", req.name)
        res.setdefault("doc_id", doc_id)
    return _with_snippet(res, snippets.rag_ingest_snippet(req.name, req.content[:200] + ("…" if len(req.content) > 200 else "")))


@app.get("/api/rag/docs")
def rag_docs(user: dict = Depends(demo_auth.require_user)):
    _ = user
    return {
        "docs": [
            {
                "doc_id": k,
                "name": v["name"],
                "chars": v.get("chars", 0),
                "chunks": v.get("chunks", 1),
            }
            for k, v in _RAG.items()
        ]
    }


class RagQueryReq(BaseModel):
    query: str
    model: str = "auto"
    top_k: int = 3


@app.post("/api/rag/query")
def rag_query(req: RagQueryReq, user: dict = Depends(demo_auth.require_user)):
    client = _client_for(user)
    res = zs.rag_query(client, req.query, n_results=req.top_k)
    extra = _ensure_routing_extra(req.model)
    snip = snippets.rag_query_snippet(req.query, req.model, extra_body=extra)
    if not res.get("ok"):
        return _with_snippet(
            {
                "content": (
                    f"Vector firewall denied/failed this retrieval: "
                    f"{res.get('message') or ('HTTP ' + str(res.get('status')))}"
                ),
                "retrieved": [],
                "trace": None,
                "rag": res,
            },
            snip,
        )
    docs = res.get("documents") or []
    if not docs:
        return _with_snippet(
            {
                "content": "No relevant documents found in the governed vector store for this query.",
                "retrieved": [],
                "trace": None,
                "rag": {"retrieved": 0, "collection": res.get("collection")},
            },
            snip,
        )

    def _doc_text(d):
        return d.get("content") or d.get("text") or ""

    context = "\n\n".join(_doc_text(d) for d in docs)
    messages = [
        {
            "role": "system",
            "content": (
                "Answer ONLY from the provided context. If the answer is not in "
                "the context, say you don't know. Do not invent facts."
            ),
        },
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {req.query}"},
    ]
    r = zs.chat(client, messages, model=req.model, max_tokens=500, extra_body=extra)
    return _with_snippet(
        {
            "content": r["content"],
            "model": r["model"],
            "retrieved": [
                {
                    "source": (d.get("metadata") or {}).get("name")
                    or (d.get("metadata") or {}).get("src")
                    or "doc",
                    "snippet": _doc_text(d)[:160],
                }
                for d in docs
            ],
            "trace": r["trace"],
            "rag": {
                "retrieved": len(docs),
                "collection": res.get("collection"),
                "scan_verdict": res.get("scan_verdict"),
            },
        },
        snip,
    )


# ───────────────────────── MCP context + tool proxies ─────────────────────────
import httpx as _httpx


def _gateway_host() -> str:
    base = GATEWAY_BASE_URL.rstrip("/")
    return base[:-3] if base.endswith("/v1") else base


def _control_detail(resp: _httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        text = (resp.text or "")[:240]
        return text or f"HTTP {resp.status_code}"
    if isinstance(data, dict):
        for key in ("error", "detail", "message"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:400]
            if isinstance(val, dict) and val.get("message"):
                return str(val["message"])[:400]
    return f"HTTP {resp.status_code}"


def _control_get(user: dict, path: str, *, timeout: float = 30.0) -> Any:
    token = user.get("_access_token") or ""
    url = f"{CONTROL_BASE_URL}{path}"
    try:
        with _httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except _httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Control plane unreachable") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_control_detail(resp))
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Malformed control response") from exc


def _control_post(user: dict, path: str, body: dict, *, timeout: float = 90.0) -> tuple[int, Any]:
    token = user.get("_access_token") or ""
    url = f"{CONTROL_BASE_URL}{path}"
    try:
        with _httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except _httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Control plane unreachable") from exc
    try:
        data = resp.json()
    except ValueError:
        data = {"error": (resp.text or "")[:400] or f"HTTP {resp.status_code}"}
    if resp.status_code >= 400:
        detail = _control_detail(resp)
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.status_code, data


def _sort_mcp_servers(servers: list[dict]) -> list[dict]:
    def _key(s: dict) -> tuple:
        connected = str(s.get("connection_status") or "").lower() == "connected"
        tools = int(s.get("tools_count") or 0)
        return (0 if connected and tools > 0 else 1 if connected else 2, -(tools), str(s.get("name") or s.get("server_slug") or ""))

    return sorted(servers, key=_key)


class McpReq(BaseModel):
    input: str
    context: dict
    model: str = "auto"


class McpToolCallReq(BaseModel):
    server_slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)


@app.post("/api/mcp/query")
def mcp_query(req: McpReq, user: dict = Depends(demo_auth.require_user)):
    ctx_prose = ", and ".join(f"the {k.replace('_', ' ')} is {v}" for k, v in req.context.items())
    messages = [
        {"role": "user", "content": f"{req.input} For this customer, {ctx_prose}."},
    ]
    extra = _ensure_routing_extra(req.model, {"mcp_context": req.context})
    r = zs.chat(
        _client_for(user),
        messages,
        model=req.model,
        max_tokens=500,
        extra_body=extra,
    )
    _act = (r["trace"] or {}).get("action")
    _note = (
        f"⚠ The firewall {_act}ed this context before the model saw it — "
        "the raw values below were NOT what reached the LLM."
        if _act in ("redact", "block", "flag")
        else None
    )
    return _with_snippet(
        {
            "content": r["content"],
            "model": r["model"],
            "context_used": req.context,
            "assembled_context": ctx_prose,
            "final_messages": messages,
            "context_firewall_action": _act,
            "context_note": _note,
            "trace": r["trace"],
        },
        snippets.mcp_snippet(req.input, req.context, req.model, extra_body=extra),
    )


@app.get("/api/mcp/servers")
def mcp_servers(user: dict = Depends(demo_auth.require_user)):
    data = _control_get(user, "/api/mcp-connector/servers/")
    rows = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) else [])
    if not isinstance(rows, list):
        rows = []
    servers = _sort_mcp_servers([r for r in rows if isinstance(r, dict)])
    return {"servers": servers, "count": len(servers)}


@app.get("/api/mcp/servers/{server_id}/tools")
def mcp_server_tools(server_id: str, user: dict = Depends(demo_auth.require_user)):
    data = _control_get(user, f"/api/mcp-connector/servers/{server_id}/tools/")
    rows = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) else [])
    if not isinstance(rows, list):
        rows = []
    tools = [t for t in rows if isinstance(t, dict)]
    return {"tools": tools, "count": len(tools)}


@app.post("/api/mcp/tools/call")
def mcp_tools_call(req: McpToolCallReq, user: dict = Depends(demo_auth.require_user)):
    status_code, data = _control_post(
        user,
        "/api/mcp-connector/tools/call/",
        {
            "server_slug": req.server_slug.strip().lower(),
            "name": req.name.strip(),
            "arguments": req.arguments if isinstance(req.arguments, dict) else {},
        },
    )
    snip = snippets.mcp_tool_call_snippet(req.server_slug, req.name, req.arguments)
    decision = None
    if isinstance(data, dict):
        decision = data.get("decision") or data.get("action") or (data.get("scan") or {}).get("decision")
    return _with_snippet(
        {
            "status_code": status_code,
            "server_slug": req.server_slug,
            "tool_name": req.name,
            "arguments": req.arguments,
            "decision": decision,
            "result": data,
        },
        snip,
    )


@app.get("/api/mcp/gateway-endpoint")
def mcp_gateway_endpoint(user: dict = Depends(demo_auth.require_user)):
    _key, session = session_keys.gateway_key_for_user(user)
    org_slug = session.get("org_slug") or (user.get("organization") or {}).get("slug") or ""
    host = _gateway_host()
    template = f"{host}/gateway/{{org_slug}}/mcp/{{server_slug}}"
    return {
        "org_slug": org_slug,
        "gateway_host": host,
        "openai_base_url": GATEWAY_BASE_URL,
        "mcp_url_template": template,
        "api_key_hint": session.get("prefix") or "",
        "auth_header": "Authorization: Bearer <YOUR_ZEROSHIELD_GATEWAY_KEY>",
        "note": "Use Pattern C JSON-RPC against mcp_url_template — not the /v1 OpenAI base URL.",
        "sdk_snippet": snippets.mcp_jsonrpc_snippet(
            org_slug or "your-org",
            "your-server-slug",
            gateway_host=host,
        ),
    }


# ───────────────────────── Files ─────────────────────────
_SUPPORTED_FILE_EXTS = (".txt", ".csv", ".md", ".pdf", ".docx")


def _extract_text(name: str, data: bytes) -> str:
    low = name.lower()
    if low.endswith(".txt") or low.endswith(".csv") or low.endswith(".md"):
        return data.decode("utf-8", errors="replace")
    if low.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            r = PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in r.pages)
        except Exception as e:
            return f"[PDF extraction failed: {e}]"
    if low.endswith(".docx"):
        try:
            import docx

            d = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in d.paragraphs)
        except Exception as e:
            return f"[DOCX extraction failed: {e}]"
    return data.decode("utf-8", errors="replace")


@app.post("/api/files/analyze")
async def files_analyze(
    file: UploadFile = File(...),
    instruction: str = Form("Summarize this document."),
    model: str = Form("auto"),
    user: dict = Depends(demo_auth.require_user),
):
    data = await file.read()
    if len(data) > 2_000_000:
        raise HTTPException(status_code=413, detail="File too large (demo cap 2MB).")
    fname = file.filename or "file"
    if not fname.lower().endswith(_SUPPORTED_FILE_EXTS):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Supported: {', '.join(_SUPPORTED_FILE_EXTS)}.",
        )
    if not data:
        raise HTTPException(status_code=400, detail="File is empty.")
    text = _extract_text(fname, data)
    if not text.strip():
        raise HTTPException(status_code=422, detail="No extractable text in file.")
    excerpt = text[:6000]
    messages = [
        {"role": "system", "content": "You analyze documents. Be concise and factual."},
        {"role": "user", "content": f"{instruction}\n\nDocument ({file.filename}):\n{excerpt}"},
    ]
    extra = _ensure_routing_extra(model)
    r = zs.chat(
        _client_for(user),
        messages,
        model=model,
        max_tokens=2048,
        extra_body=extra,
    )
    return _with_snippet(
        {
            "filename": file.filename,
            "extracted_chars": len(text),
            "content": r["content"],
            "model": r.get("model"),
            "trace": r["trace"],
            # Forward block envelope so the UI can show why (not only verdict: block).
            "blocked": bool(r.get("blocked") or (r.get("trace") or {}).get("action") == "block"),
            "message": r.get("message") or (r.get("trace") or {}).get("guard_reason")
                or (r.get("trace") or {}).get("detail") or "",
            "category": r.get("category") or (r.get("trace") or {}).get("threat_type") or "",
        },
        snippets.files_snippet(instruction, model, extra_body=extra),
    )


# ───────────────────────── SDK preview (no gateway call) ─────────────────────────
class PreviewReq(BaseModel):
    kind: str = "chat"
    messages: list[dict] | None = None
    model: str = "auto"
    input: str | None = None
    context: dict | None = None
    extra_body: dict | None = None
    stream: bool = False
    max_tokens: int = 512
    risk_weight: float | None = None
    cost_weight: float | None = None
    latency_weight: float | None = None
    data_sensitivity: str | None = None
    server_slug: str | None = None
    tool_name: str | None = None
    arguments: dict | None = None
    org_slug: str | None = None


@app.post("/api/sdk/preview")
def sdk_preview(req: PreviewReq, user: dict = Depends(demo_auth.require_user)):
    _ = user
    if req.kind == "route":
        weight_prefs: dict[str, Any] = {}
        for k in ("risk_weight", "cost_weight", "latency_weight"):
            v = getattr(req, k)
            if v is not None:
                weight_prefs[k] = v
        if req.data_sensitivity:
            weight_prefs["data_sensitivity"] = req.data_sensitivity
        prefs = _routing_prefs_for_model(req.model, **weight_prefs)
        return {
            "sdk_snippet": snippets.route_snippet(req.input or "", req.model, prefs),
            "gateway_base_url": GATEWAY_BASE_URL,
        }
    if req.kind == "mcp":
        extra = _ensure_routing_extra(req.model, {"mcp_context": req.context or {}})
        return {
            "sdk_snippet": snippets.mcp_snippet(
                req.input or "", req.context or {}, req.model, extra_body=extra
            ),
            "gateway_base_url": GATEWAY_BASE_URL,
        }
    if req.kind == "mcp_tool":
        slug = (req.server_slug or "your-server").strip()
        tool = (req.tool_name or "echo").strip()
        args = req.arguments if isinstance(req.arguments, dict) else {}
        return {
            "sdk_snippet": snippets.mcp_tool_call_snippet(slug, tool, args),
            "gateway_base_url": GATEWAY_BASE_URL,
            "jsonrpc_snippet": snippets.mcp_jsonrpc_snippet(
                (req.org_slug or "your-org").strip(),
                slug,
                tool,
                args,
                gateway_host=_gateway_host(),
            ),
        }
    if req.kind == "validate":
        extra = _ensure_routing_extra(req.model, req.extra_body)
        return {
            "sdk_snippet": snippets.validate_snippet(
                req.input or "", req.model, extra_body=extra
            ),
            "gateway_base_url": GATEWAY_BASE_URL,
        }
    if req.kind == "rag_query":
        extra = _ensure_routing_extra(req.model, req.extra_body)
        return {
            "sdk_snippet": snippets.rag_query_snippet(
                req.input or "", req.model, extra_body=extra
            ),
            "gateway_base_url": GATEWAY_BASE_URL,
        }
    msgs = req.messages or [{"role": "user", "content": req.input or ""}]
    extra = _ensure_routing_extra(req.model, req.extra_body)
    return {
        "sdk_snippet": snippets.chat_snippet(
            msgs,
            req.model,
            stream=req.stream,
            extra_body=extra,
            max_tokens=req.max_tokens,
        ),
        "gateway_base_url": GATEWAY_BASE_URL,
    }


# ───────────────────────── static frontend ─────────────────────────
import os as _os

_FRONTEND = _os.path.join(_os.path.dirname(__file__), "..", "frontend")
if _os.path.isdir(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")

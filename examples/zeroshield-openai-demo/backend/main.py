"""
ZeroShield OpenAI-SDK Demo — backend.

A reference application proving that a customer accesses ZeroShield's full
capability set (routing, governance, input/output validation, RAG + MCP context
security) while using ONLY the stock OpenAI SDK. The backend orchestrates
client-side concerns (document retrieval, file text extraction, context
assembly) and delegates every AI call + all security to the ZeroShield gateway
through `openai.OpenAI(base_url=..., api_key=...)`.

Run:
    export ZEROSHIELD_API_KEY=...        # a ZeroShield gateway key
    export ZEROSHIELD_BASE_URL=https://aimeshgateway.zeroshield.ai/v1
    uvicorn main:app --port 8800
"""
from __future__ import annotations

import io
import json
import re
from collections import Counter
from typing import Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import zeroshield_client as zs

app = FastAPI(title="ZeroShield OpenAI-SDK Demo")

# In-memory RAG store (demo): {doc_id: {"name":..., "chunks":[...]}}
_RAG: dict[str, dict] = {}


def _client():
    try:
        return zs.make_client()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ───────────────────────── Health / models ─────────────────────────
@app.get("/api/health")
def health():
    try:
        models = zs.list_models(_client())
        return {"ok": True, "base_url": zs.GATEWAY_BASE_URL, "models": models}
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": str(e)})


@app.get("/api/models")
def models():
    return {"models": zs.list_models(_client())}


@app.get("/api/observability")
def observability():
    """Real-time firewall governance for the audit/observability panel (§6)."""
    return zs.observability(_client())


# ───────────────────────── 1. Chat ─────────────────────────
class ChatReq(BaseModel):
    messages: list[dict]
    model: str = "auto"
    max_tokens: int = 512


@app.post("/api/chat")
def chat(req: ChatReq):
    return zs.chat(_client(), req.messages, model=req.model, max_tokens=req.max_tokens)


@app.post("/api/chat/stream")
def chat_stream(req: ChatReq):
    client = _client()

    def gen():
        for kind, payload in zs.chat_stream(client, req.messages, model=req.model, max_tokens=req.max_tokens):
            if kind == "delta":
                yield f"data: {json.dumps({'delta': payload})}\n\n"
            elif kind == "trace":
                yield f"data: {json.dumps({'trace': payload})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ───────────────────────── 4. Multi-model routing ─────────────────────────
class RouteReq(BaseModel):
    input: str
    model: str = "auto"
    risk_weight: float | None = None
    cost_weight: float | None = None
    latency_weight: float | None = None
    data_sensitivity: str | None = None


@app.post("/api/route")
def route(req: RouteReq):
    # Use the same firewalled chat path as /api/chat (the Responses path returned a
    # null-trace block for every model). routing_preferences (weights + sensitivity)
    # are threaded into the gateway adjudicator and surfaced in the routing trace.
    prefs: dict[str, Any] = {}
    for k in ("risk_weight", "cost_weight", "latency_weight"):
        v = getattr(req, k)
        if v is not None:
            prefs[k] = v
    if req.data_sensitivity:
        prefs["data_sensitivity"] = req.data_sensitivity
    extra_body: dict[str, Any] = {"routing_preferences": prefs} if prefs else {}
    r = zs.chat(_client(), [{"role": "user", "content": req.input}], model=req.model,
                extra_body=extra_body or None, max_tokens=400)
    return {"output_text": r["content"], "model": r["model"], "trace": r["trace"]}


# ───────────────────────── 6. Output validation ─────────────────────────
class ValidateReq(BaseModel):
    input: str
    model: str = "auto"


@app.post("/api/validate")
def validate(req: ValidateReq):
    """Send (possibly unsafe) text and surface the gateway's verdict + trace.

    The gateway scans the INPUT (PII/credentials/injection/policy) and the
    OUTPUT (PII/credential/IP/hallucination guardrails). The full pipeline_trace
    is returned for the visualizer.
    """
    r = zs.chat(_client(), [{"role": "user", "content": req.input}], model=req.model, max_tokens=400)
    return {"content": r["content"], "model": r["model"], "trace": r["trace"]}


# ───────────────────────── 2. RAG ─────────────────────────
def _chunk(text: str, size: int = 600) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def _score(query: str, chunk: str) -> int:
    q = Counter(re.findall(r"[a-z0-9]+", query.lower()))
    c = Counter(re.findall(r"[a-z0-9]+", chunk.lower()))
    return sum(min(q[w], c[w]) for w in q)


class RagIngestReq(BaseModel):
    name: str
    content: str


@app.post("/api/rag/ingest")
def rag_ingest(req: RagIngestReq):
    # Ingest through the PLATFORM vector firewall (Pinecone + ingest-time poison/PII
    # scan), NOT a local dict — so a poisoned/credential-laden doc is governed.
    res = zs.rag_ingest(_client(), req.name, req.content)
    if res.get("ok"):
        doc_id = re.sub(r"[^a-z0-9]+", "-", req.name.lower())[:40] or "doc"
        # Track the real vector/chunk count the gateway accepted (UI shows "N chunks").
        chunks = res.get("ingested_count") or 1
        _RAG[doc_id] = {"name": req.name, "chars": len(req.content), "chunks": chunks}
        # Surface chunks/name on the ingest response so the UI chip renders a real
        # count immediately (the frontend reads them off this response, not /docs).
        res.setdefault("chunks", chunks)
        res.setdefault("name", req.name)
        res.setdefault("doc_id", doc_id)
    return res


@app.get("/api/rag/docs")
def rag_docs():
    return {"docs": [{"doc_id": k, "name": v["name"], "chars": v.get("chars", 0),
                      "chunks": v.get("chunks", 1)} for k, v in _RAG.items()]}


class RagQueryReq(BaseModel):
    query: str
    model: str = "auto"
    top_k: int = 3


@app.post("/api/rag/query")
def rag_query(req: RagQueryReq):
    # Retrieve from the PLATFORM vector store WITH grounding + poison-scan via the
    # gateway's /v1/rag/query (real Pinecone retrieval, governed by the vector
    # policy), THEN synthesize the answer through the firewalled chat path.
    res = zs.rag_query(_client(), req.query, n_results=req.top_k)
    if not res.get("ok"):
        return {"content": f"Vector firewall denied/failed this retrieval: {res.get('message') or ('HTTP ' + str(res.get('status')))}",
                "retrieved": [], "trace": None, "rag": res}
    docs = res.get("documents") or []
    if not docs:
        return {"content": "No relevant documents found in the governed vector store for this query.",
                "retrieved": [], "trace": None, "rag": {"retrieved": 0, "collection": res.get("collection")}}
    # The gateway returns the chunk text under "content" (Pinecone) — fall back to
    # "text" for forward-compat. Reading the wrong key yields an empty context and
    # the synthesizer hallucinates, so this MUST match the gateway response shape.
    def _doc_text(d):
        return d.get("content") or d.get("text") or ""
    context = "\n\n".join(_doc_text(d) for d in docs)
    messages = [
        {"role": "system", "content": "Answer ONLY from the provided context. If the answer is not in the context, say you don't know. Do not invent facts."},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {req.query}"},
    ]
    r = zs.chat(_client(), messages, model=req.model, max_tokens=500)
    return {"content": r["content"], "model": r["model"],
            "retrieved": [{"source": (d.get("metadata") or {}).get("name") or (d.get("metadata") or {}).get("src") or "doc", "snippet": _doc_text(d)[:160]} for d in docs],
            "trace": r["trace"],
            "rag": {"retrieved": len(docs), "collection": res.get("collection"), "scan_verdict": res.get("scan_verdict")}}


# ───────────────────────── 3. MCP context ─────────────────────────
class McpReq(BaseModel):
    input: str
    context: dict
    model: str = "auto"


@app.post("/api/mcp/query")
def mcp_query(req: McpReq):
    # Structured MCP context (customer profile / CRM / ticket) is passed to the
    # gateway as `agent_data` (the gateway scans + governs it for PII/secrets and
    # records MCP telemetry) and summarized into the prompt for the model.
    # Render the structured context as a single natural sentence. A `key: value`
    # block — or a "Label: ..." prefix — trips the gateway's static ChatML /
    # role-spoof injection signatures (a legitimate firewall behavior; see the MCP
    # validation report). The structured dict is ALSO sent verbatim as `agent_data`
    # so the gateway scans it for PII/secrets and records MCP governance telemetry.
    ctx_prose = ", and ".join(f"the {k.replace('_', ' ')} is {v}" for k, v in req.context.items())
    messages = [
        {"role": "user", "content": f"{req.input} For this customer, {ctx_prose}."},
    ]
    # Scenario 4 shape: structured context injected via extra_body.mcp_context.
    # The gateway scans it (PII/secrets) and records MCP governance telemetry.
    r = zs.chat(_client(), messages, model=req.model, max_tokens=500,
                extra_body={"mcp_context": req.context})
    _act = (r["trace"] or {}).get("action")
    _note = (f"⚠ The firewall {_act}ed this context before the model saw it — the raw values below were NOT what reached the LLM."
             if _act in ("redact", "block", "flag") else None)
    # Surface the three inspection views the requirement names: RAW structured
    # context (as injected), the ASSEMBLED natural-language fragment, and the FINAL
    # payload of messages actually sent through the OpenAI SDK to the gateway.
    return {"content": r["content"], "model": r["model"], "context_used": req.context,
            "assembled_context": ctx_prose, "final_messages": messages,
            "context_firewall_action": _act, "context_note": _note, "trace": r["trace"]}


# ───────────────────────── 5. File upload + analysis ─────────────────────────
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
async def files_analyze(file: UploadFile = File(...), instruction: str = Form("Summarize this document."),
                        model: str = Form("auto")):
    data = await file.read()
    if len(data) > 2_000_000:
        raise HTTPException(status_code=413, detail="File too large (demo cap 2MB).")
    fname = file.filename or "file"
    if not fname.lower().endswith(_SUPPORTED_FILE_EXTS):
        raise HTTPException(status_code=415,
                            detail=f"Unsupported file type. Supported: {', '.join(_SUPPORTED_FILE_EXTS)}.")
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
    max_out = 2048
    r = zs.chat(_client(), messages, model=model, max_tokens=max_out)
    content = r["content"]
    return {"filename": file.filename, "extracted_chars": len(text),
            "content": content, "model": r["model"], "trace": r["trace"]}


# ───────────────────────── static frontend ─────────────────────────
import os as _os
_FRONTEND = _os.path.join(_os.path.dirname(__file__), "..", "frontend")
if _os.path.isdir(_FRONTEND):
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")

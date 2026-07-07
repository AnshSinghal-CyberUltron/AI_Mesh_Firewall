"""FastAPI server — exposes demo UI and API backed by stock OpenAI SDK only."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import authenticate, require_superuser
from app.config import BASE_URL, DEMO_HOST, DEMO_PORT, RAG_COLLECTION
from app.extractors import extract_text
from app.gateway_client import ZeroShieldClient

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="ZeroShield OpenAI SDK Demo", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Every data route is gated to platform superusers; only the login, health,
# UI shell and static assets are public.
GUARD = [Depends(require_superuser)]

_sessions: dict[str, list[dict]] = {}


def _client() -> ZeroShieldClient:
    try:
        return ZeroShieldClient()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    model: str = "auto"
    stream: bool = False
    mcp_context: dict | None = None
    routing_preferences: dict | None = None


class RespondRequest(BaseModel):
    input: str
    model: str = "auto"
    stream: bool = False
    mcp_context: dict | None = None
    routing_preferences: dict | None = None
    scenario: str | None = None


class RagIngestRequest(BaseModel):
    collection: str = RAG_COLLECTION
    texts: list[str] = Field(default_factory=list)


class RagQueryRequest(BaseModel):
    collection: str = RAG_COLLECTION
    query: str
    synthesize: bool = True
    model: str = "auto"


@app.on_event("startup")
def _startup() -> None:
    from app.config import API_KEY, BASE_URL, _debug_log

    _debug_log(
        location="server.py:startup",
        message="demo server started",
        data={"has_api_key": bool(API_KEY), "base_url": BASE_URL},
        hypothesis_id="H1",
    )


class LoginRequest(BaseModel):
    email: str
    password: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "gateway_base_url": BASE_URL, "demo_host": DEMO_HOST, "demo_port": DEMO_PORT}


@app.get("/api/readiness", dependencies=GUARD)
def readiness() -> dict:
    try:
        probe = _client().readiness_probe()
        return {"gateway_base_url": BASE_URL, **probe}
    except HTTPException:
        raise
    except Exception as exc:
        return {
            "ok": False,
            "gateway_base_url": BASE_URL,
            "gateway_reachable": False,
            "models_total": 0,
            "models_healthy": 0,
            "models": [],
            "issues": [
                {
                    "issue": "demo_probe_failed",
                    "summary": "Readiness probe failed.",
                    "plain_text": "The demo could not complete its startup checks right now.",
                    "next_step": f"Retry after gateway restart. ({str(exc)[:200]})",
                }
            ],
        }


@app.post("/api/login")
async def login(req: LoginRequest) -> dict:
    """Sign in against control and enforce superuser. Public (the gate itself)."""
    return await authenticate(req.email, req.password)


@app.get("/api/models", dependencies=GUARD)
def models() -> dict:
    return {"models": _client().list_models()}


@app.post("/api/chat", dependencies=GUARD)
def chat(req: ChatRequest) -> dict:
    sid = req.session_id or str(uuid.uuid4())
    history = _sessions.setdefault(sid, [])
    history.append({"role": "user", "content": req.message})
    if req.stream:
        raise HTTPException(status_code=400, detail="Use /api/chat/stream for streaming")
    result = _client().chat(
        history,
        model=req.model,
        mcp_context=req.mcp_context,
        routing_preferences=req.routing_preferences,
    )
    history.append({"role": "assistant", "content": result.get("content", "")})
    return {"session_id": sid, **result}


@app.post("/api/chat/stream", dependencies=GUARD)
def chat_stream(req: ChatRequest):
    sid = req.session_id or str(uuid.uuid4())
    history = _sessions.setdefault(sid, [])
    history.append({"role": "user", "content": req.message})

    def gen():
        client = _client()
        stream = client.chat(
            history,
            model=req.model,
            stream=True,
            mcp_context=req.mcp_context,
            routing_preferences=req.routing_preferences,
        )
        full = []
        for ev in stream:
            if ev.get("type") == "delta":
                full.append(ev.get("content", ""))
            yield f"data: {json.dumps(ev)}\n\n"
        history.append({"role": "assistant", "content": "".join(full)})
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/respond", dependencies=GUARD)
def respond(req: RespondRequest) -> dict:
    client = _client()
    if req.scenario == "mcp":
        return client.scenario_mcp(req.input, customer_id="123", model=req.model)
    if req.scenario == "routing":
        sens = (req.routing_preferences or {}).get("data_sensitivity", "standard")
        return client.scenario_routing(req.input, model=req.model, sensitivity=sens)
    if req.scenario == "guardrail":
        return client.scenario_guardrail_probe(req.input, model=req.model)
    if req.scenario == "basic":
        return client.scenario_basic_chat(req.input, model=req.model)
    return client.respond(
        req.input,
        model=req.model,
        mcp_context=req.mcp_context,
        routing_preferences=req.routing_preferences,
    )


@app.post("/api/respond/stream", dependencies=GUARD)
def respond_stream(req: RespondRequest):
    def gen():
        stream = _client().respond(
            req.input,
            model=req.model,
            stream=True,
            mcp_context=req.mcp_context,
            routing_preferences=req.routing_preferences,
        )
        for ev in stream:
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/rag/ingest", dependencies=GUARD)
def rag_ingest(req: RagIngestRequest) -> dict:
    docs = [{"text": t, "metadata": {"source": "demo"}} for t in req.texts if t.strip()]
    if not docs:
        raise HTTPException(status_code=400, detail="No documents to ingest")
    result = _client().rag_ingest(req.collection, docs)
    if result.get("error") and result.get("status", 200) >= 400:
        # Surface gateway verdict without crashing the demo UI (e.g. chroma upsert).
        return result
    return result


@app.post("/api/rag/query", dependencies=GUARD)
def rag_query(req: RagQueryRequest) -> dict:
    try:
        client = _client()
        if req.synthesize:
            return client.scenario_rag(req.collection, req.query, model=req.model)
        return client.rag_query(req.collection, req.query)
    except RuntimeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc


@app.post("/api/files/analyze", dependencies=GUARD)
async def analyze_files(
    model: str = "auto",
    files: list[UploadFile] = File(...),
) -> dict:
    texts = []
    for f in files:
        data = await f.read()
        try:
            texts.append({"name": f.filename, "text": extract_text(f.filename or "file.txt", data)})
        except ValueError as exc:
            texts.append({"name": f.filename, "error": str(exc)})
    combined = "\n\n".join(
        f"### {t['name']}\n{t.get('text', t.get('error', ''))}" for t in texts
    )
    prompt = f"Analyze the following uploaded documents. Summarize key points and flag any risks.\n\n{combined[:120000]}"
    result = _client().respond(prompt, model=model)
    return {"files": texts, "analysis": result}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("app.server:app", host=DEMO_HOST, port=DEMO_PORT, reload=False)


if __name__ == "__main__":
    main()

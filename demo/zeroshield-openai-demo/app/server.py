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
from app.config import BASE_URL, DEMO_HOST, DEMO_PORT, DEMO_REQUIRE_APP_LOGIN, RAG_COLLECTION
from app.extractors import extract_text
from app.gateway_client import ZeroShieldClient
from app.sdk_scenarios import list_sdk_scenarios_public
from app.status_reason import REASON_BLOCKED_POLICY, attach_files_response, attach_status_reason
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="ZeroShield OpenAI SDK Demo", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional in-app gate:
# - False: rely on nginx /demo Basic Auth only (single login prompt).
# - True: require platform superuser JWT for each API request.
GUARD = [Depends(require_superuser)] if DEMO_REQUIRE_APP_LOGIN else []

_sessions: dict[str, list[dict]] = {}


def _is_policy_blocked(result: dict[str, Any]) -> bool:
    """True when the gateway blocked this turn during input/policy analysis."""
    reason = result.get("status_reason") if isinstance(result.get("status_reason"), dict) else {}
    if reason.get("code") == REASON_BLOCKED_POLICY:
        return True
    pipeline = result.get("pipeline") if isinstance(result.get("pipeline"), dict) else {}
    action = str(pipeline.get("action") or "").lower()
    if action != "block":
        return False
    blocked_by = str(pipeline.get("blocked_by") or "").lower()
    code = str(pipeline.get("code") or "").lower()
    if blocked_by in ("input_scan", "policy", "output_guardrail", "") or code in (
        "content_filter",
        "content_blocked",
        "prompt_injection",
    ):
        return True
    zs = result.get("zeroshield") if isinstance(result.get("zeroshield"), dict) else {}
    threat = str(zs.get("threat_type") or "").lower()
    return threat in ("content_filter", "content_blocked", "prompt_injection")


def _stream_event_blocked(event: dict[str, Any]) -> bool:
    if event.get("type") not in ("error", "trace"):
        return False
    return _is_policy_blocked(event)


def _rollback_last_user_turn(history: list[dict]) -> None:
    """Drop a blocked user turn so later messages are not re-scanned with poisoned context."""
    if history and history[-1].get("role") == "user":
        history.pop()


def _attach_rag_view(view: dict[str, Any]) -> dict[str, Any]:
    """Normalize gateway RAG payloads and attach user-facing status_reason."""
    if not isinstance(view, dict):
        return view
    status = int(view.get("status") or 0)
    if view.get("error") is None and status >= 400:
        view["error"] = True
    payload = view.get("result") if isinstance(view.get("result"), dict) else {}
    raw = view.get("raw") if isinstance(view.get("raw"), dict) else {}
    if not view.get("zeroshield"):
        zs = payload.get("zeroshield") if isinstance(payload.get("zeroshield"), dict) else {}
        if not zs:
            code = str(payload.get("code") or raw.get("code") or view.get("code") or "").strip()
            message = str(payload.get("message") or raw.get("message") or view.get("message") or "").strip()
            if code or message:
                view["zeroshield"] = {"code": code, "message": message, "detail": message}
    if not view.get("pipeline") and isinstance(view.get("pipeline_audit"), dict):
        from app.pipeline import build_rag_pipeline_view

        view["pipeline"] = build_rag_pipeline_view(
            zeroshield=view.get("zeroshield") if isinstance(view.get("zeroshield"), dict) else {},
            pipeline_audit=view.get("pipeline_audit"),
        )
    return attach_status_reason(view, context="rag")


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
    guardrail_vector: str | None = None
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
    import logging

    from app.config import API_KEY, BASE_URL, RAG_COLLECTION, _debug_log

    _debug_log(
        location="server.py:startup",
        message="demo server started",
        data={"has_api_key": bool(API_KEY), "base_url": BASE_URL},
        hypothesis_id="H1",
    )
    if not API_KEY:
        logging.getLogger("demo.server").warning(
            "ZEROSHIELD_API_KEY is unset — gateway calls will fail until configured."
        )
        return
    try:
        rag = ZeroShieldClient().rag_readiness_probe(RAG_COLLECTION)
        if not rag.get("ok"):
            logging.getLogger("demo.server").warning(
                "RAG not ready for collection=%s: %s — %s",
                rag.get("collection"),
                rag.get("message"),
                rag.get("next_step"),
            )
    except Exception as exc:
        logging.getLogger("demo.server").warning("RAG readiness probe skipped: %s", exc)


class LoginRequest(BaseModel):
    email: str
    password: str


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "gateway_base_url": BASE_URL,
        "demo_host": DEMO_HOST,
        "demo_port": DEMO_PORT,
        "app_login_required": DEMO_REQUIRE_APP_LOGIN,
    }


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
    """Models the current org API key may call — same list as GET /v1/models on the gateway."""
    listed = _client().list_models()
    org = next((str(m.get("owned_by") or "").strip() for m in listed if m.get("owned_by")), "")
    return {
        "models": listed,
        "default_model": "auto",
        "org": org,
        "count": len(listed),
    }


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
    if _is_policy_blocked(result):
        _rollback_last_user_turn(history)
        result["session_reset"] = True
    else:
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
        blocked = False
        for ev in stream:
            if _stream_event_blocked(ev):
                blocked = True
            if ev.get("type") == "delta":
                full.append(ev.get("content", ""))
            yield f"data: {json.dumps(ev)}\n\n"
        if blocked:
            _rollback_last_user_turn(history)
        elif full:
            history.append({"role": "assistant", "content": "".join(full)})
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid, 'session_reset': blocked})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/sdk-scenarios")
def sdk_scenarios_catalog() -> dict:
    return {"scenarios": list_sdk_scenarios_public()}


@app.post("/api/respond", dependencies=GUARD)
def respond(req: RespondRequest) -> dict:
    client = _client()
    if req.scenario == "mcp":
        return client.scenario_mcp(req.input, mcp_context=req.mcp_context, model=req.model)
    if req.scenario == "routing":
        return client.scenario_routing(
            req.input,
            model=req.model,
            routing_preferences=req.routing_preferences,
        )
    if req.scenario == "guardrail":
        vector = str(req.guardrail_vector or "attack").strip().lower()
        return client.scenario_guardrail_probe(req.input, model=req.model, vector=vector)
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


@app.get("/api/rag/readiness", dependencies=GUARD)
def rag_readiness(collection: str = RAG_COLLECTION) -> dict:
    """Probe gateway RAG policy + vector DB path for the demo collection."""
    try:
        return _client().rag_readiness_probe(collection)
    except Exception as exc:
        return {
            "ok": False,
            "collection": collection,
            "message": str(exc)[:300],
            "next_step": "Ensure gateway is up and ZEROSHIELD_API_KEY is set, then run scripts/bootstrap_rag.py.",
        }


@app.post("/api/rag/ingest", dependencies=GUARD)
def rag_ingest(req: RagIngestRequest) -> dict:
    docs = [{"text": t, "metadata": {"source": "demo"}} for t in req.texts if t.strip()]
    if not docs:
        raise HTTPException(status_code=400, detail="No documents to ingest")
    result = _attach_rag_view(_client().rag_ingest(req.collection, docs))
    return result


@app.post("/api/rag/query", dependencies=GUARD)
def rag_query(req: RagQueryRequest) -> dict:
    try:
        client = _client()
        if req.synthesize:
            result = client.scenario_rag(req.collection, req.query, model=req.model)
        else:
            result = _attach_rag_view(client.rag_query(req.collection, req.query))
        if isinstance(result, dict) and req.synthesize:
            retrieval = result.get("retrieval") if isinstance(result.get("retrieval"), dict) else {}
            answer = result.get("answer") if isinstance(result.get("answer"), dict) else {}
            if retrieval.get("error") or int(retrieval.get("status") or 0) >= 400:
                result["error"] = True
            if isinstance(retrieval.get("pipeline"), dict):
                result["rag_pipeline"] = retrieval["pipeline"]
                result["pipeline"] = retrieval["pipeline"]
            if isinstance(answer.get("pipeline"), dict):
                result["synthesis_pipeline"] = answer["pipeline"]
            ret_reason = retrieval.get("status_reason") if isinstance(retrieval.get("status_reason"), dict) else {}
            ans_reason = answer.get("status_reason") if isinstance(answer.get("status_reason"), dict) else {}
            if retrieval.get("error") or int(retrieval.get("status") or 0) >= 400:
                result["status_reason"] = ret_reason or ans_reason
            elif ret_reason.get("code") in ("rag_vector_unavailable", "rag_access_denied"):
                result["status_reason"] = ret_reason
            elif ans_reason:
                result["status_reason"] = ans_reason
                result["retrieval_status_reason"] = ret_reason
            elif ret_reason:
                result["status_reason"] = ret_reason
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc


@app.post("/api/files/analyze", dependencies=GUARD)
async def analyze_files(
    model: str = "auto",
    files: list[UploadFile] = File(...),
) -> dict:
    documents: list[dict[str, Any]] = []
    file_warnings: list[str] = []
    for f in files:
        data = await f.read()
        name = f.filename or "file.txt"
        try:
            text = extract_text(name, data)
            documents.append({"name": name, "text": text})
        except ValueError as exc:
            err = str(exc)
            documents.append({"name": name, "error": err})
            file_warnings.append(f"{name}: {err}")

    files_manifest: list[dict[str, Any]] = []
    for doc in documents:
        entry: dict[str, Any] = {"name": doc.get("name") or "file"}
        if doc.get("text"):
            entry["status"] = "ok"
            entry["chars"] = len(str(doc["text"]))
        else:
            entry["status"] = "error"
            entry["error"] = doc.get("error", "unreadable")
        files_manifest.append(entry)

    readable = [d for d in documents if d.get("text")]
    if not readable:
        return attach_files_response(
            {
                "files_manifest": files_manifest,
                "analysis": None,
                "error": True,
                "status": 400,
                "message": "Uploaded files could not be parsed.",
            }
        )

    analysis = _client().scenario_files_analyze(readable, model=model)
    payload: dict[str, Any] = {"files_manifest": files_manifest, "analysis": analysis}
    if isinstance(analysis, dict) and analysis.get("extraction_meta"):
        payload["extraction_meta"] = analysis["extraction_meta"]
    if file_warnings:
        payload["file_warnings"] = file_warnings
    return attach_files_response(payload)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("app.server:app", host=DEMO_HOST, port=DEMO_PORT, reload=False)


if __name__ == "__main__":
    main()

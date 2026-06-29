"""
ZeroShield demo — thin wrapper around the STOCK OpenAI SDK.

CORE RULE: this module never imports or calls a provider SDK (anthropic, google,
etc.). Every AI request goes through `openai.OpenAI` pointed at the ZeroShield
gateway. The gateway performs routing, input/output validation, governance, and
RAG/MCP context scanning — the client only changes `base_url` + `api_key`.

The wrapper additionally captures the ZeroShield-specific response envelope
(`zeroshield` metadata + `pipeline_trace`) that the gateway returns alongside the
standard OpenAI payload, so the demo UI can render the routing/validation
visualizer. These extras ride on the SAME OpenAI-compatible response — we read
them from the raw HTTP body via the SDK's `.with_raw_response` accessor.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

from openai import OpenAI, APIStatusError

GATEWAY_BASE_URL = os.environ.get("ZEROSHIELD_BASE_URL", "https://aimeshgateway.zeroshield.ai/v1")
GATEWAY_API_KEY = os.environ.get("ZEROSHIELD_API_KEY", "")


def make_client() -> OpenAI:
    """The ONE place an OpenAI client is constructed — drop-in, two fields."""
    if not GATEWAY_API_KEY:
        raise RuntimeError("ZEROSHIELD_API_KEY is not set")
    return OpenAI(api_key=GATEWAY_API_KEY, base_url=GATEWAY_BASE_URL, timeout=90, max_retries=1)


def _zs(d: dict) -> dict:
    return d.get("zeroshield") or {}


def _error_body(e: APIStatusError) -> dict:
    """The gateway's firewall-block envelope (carries pipeline_trace + verdict)."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        return body
    try:
        return json.loads(e.response.text)
    except Exception:
        return {"message": str(e)}


def _blocked_result(e: APIStatusError) -> dict:
    """Turn a firewall block (403/422/503) into a structured result with the trace,
    so the demo VISUALIZES the block instead of erroring out."""
    body = _error_body(e)
    synth = {
        "zeroshield": {
            "request_id": body.get("request_id"),
            "action": "block",
            "threat_type": body.get("category") or body.get("code"),
            "detail": body.get("message"),
            "processing_time_ms": (body.get("pipeline_trace") or {}).get("total_latency_ms"),
            "routing": (body.get("pipeline_trace") or {}).get("routing") or {},
        },
        "pipeline_trace": body.get("pipeline_trace") or {},
    }
    return {"content": "", "model": None, "blocked": True, "status": e.status_code,
            "message": body.get("message"), "category": body.get("category"),
            "trace": summarize_trace(synth), "raw": body}


def summarize_trace(body: dict) -> dict:
    """Normalize the gateway envelope into a compact shape for the visualizer."""
    zs = _zs(body)
    routing = zs.get("routing") or {}
    trace = body.get("pipeline_trace") or {}
    stages = [
        {
            "name": s.get("name"),
            "action": s.get("action"),
            "detail": s.get("detail"),
            "latency_ms": s.get("latency_ms"),
            "matched_policies": s.get("matched_policies") or [],
            "matched_rules": s.get("matched_rules") or [],
            "threat_type": s.get("threat_type"),
        }
        for s in trace.get("stages", [])
    ]
    return {
        "request_id": zs.get("request_id"),
        "action": zs.get("action"),
        "threat_type": zs.get("threat_type"),
        "confidence": zs.get("confidence"),
        "matched_patterns": zs.get("matched_patterns") or [],
        "processing_time_ms": zs.get("processing_time_ms"),
        "guard_action": zs.get("guard_action"),
        "guard_reason": zs.get("guard_reason"),
        "routing": {
            "requested": routing.get("original_model"),
            "selected": routing.get("selected_model") or routing.get("routed_model"),
            "rerouted": routing.get("rerouted"),
            "reason": routing.get("routing_reason") or routing.get("reroute_reason"),
            "decision_source": routing.get("decision_source"),
            "fallback_reason_code": routing.get("fallback_reason_code"),
            "weights": routing.get("weights"),
            "compliance": routing.get("compliance_requirements"),
            "data_sensitivity": routing.get("data_sensitivity"),
        },
        "stages": stages,
    }


# ── Chat (chat.completions) — returns the FULL pipeline_trace for the visualizer ──
def chat(client: OpenAI, messages: list[dict], model: str = "auto",
         extra_body: dict | None = None, extra_headers: dict | None = None,
         max_tokens: int = 512) -> dict:
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=model, messages=messages, max_tokens=max_tokens,
            extra_body=extra_body or None, extra_headers=extra_headers or None,
        )
    except APIStatusError as e:
        return _blocked_result(e)
    body = json.loads(raw.text)
    content = ""
    try:
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    except Exception:
        content = ""
    return {"content": content, "model": body.get("model"), "trace": summarize_trace(body), "raw": body}


def chat_stream(client: OpenAI, messages: list[dict], model: str = "auto",
                extra_body: dict | None = None, extra_headers: dict | None = None,
                max_tokens: int = 512) -> Iterator[str]:
    """Yield content deltas. Streaming runs through the SAME stock SDK."""
    try:
        stream = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, stream=True,
            extra_body=extra_body or None, extra_headers=extra_headers or None,
        )
        zsframe = None
        ptframe = None
        for chunk in stream:
            # The gateway rides a terminal frame on the stream carrying both the
            # `zeroshield` verdict AND the full 9-stage `pipeline_trace` (gateway
            # FULL-PIPELINE-ON-STREAM). Capture both so streamed chats render the
            # SAME complete pipeline as non-streamed ones.
            extra = getattr(chunk, "model_extra", None) or {}
            if isinstance(extra.get("zeroshield"), dict):
                zsframe = extra["zeroshield"]
            if isinstance(extra.get("pipeline_trace"), dict):
                ptframe = extra["pipeline_trace"]
            try:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield ("delta", delta)
            except Exception:
                continue
        if zsframe or ptframe:
            yield ("trace", summarize_trace({"zeroshield": zsframe or {}, "pipeline_trace": ptframe or {}}))
    except APIStatusError as e:
        body = _error_body(e)
        yield ("delta", f"\n[⛔ ZeroShield blocked this request — {body.get('message') or e.status_code}]")
        yield ("trace", _blocked_result(e)["trace"])


# ── Responses API (the user's primary scenarios) ──
def responses_create(client: OpenAI, input_text: str, model: str = "auto",
                     extra_body: dict | None = None, extra_headers: dict | None = None) -> dict:
    try:
        raw = client.responses.with_raw_response.create(
            model=model, input=input_text,
            extra_body=extra_body or None, extra_headers=extra_headers or None,
        )
    except APIStatusError as e:
        r = _blocked_result(e)
        return {"output_text": "", "model": None, "trace": r["trace"], "blocked": True,
                "status": r["status"], "message": r["message"], "raw": r["raw"]}
    body = json.loads(raw.text)
    return {"output_text": body.get("output_text", ""), "model": body.get("model"),
            "trace": summarize_trace(body), "raw": body}


def list_models(client: OpenAI) -> list[str]:
    return [m.id for m in client.models.list().data]


def observability(client: OpenAI) -> dict:
    """Real-time firewall observability via the SAME gateway key (SDK-native GET).

    This is a management call (not an AI request); it reaches `/v1/observability`
    through the OpenAI client's transport, so an SDK-only operator still sees
    firewall mode, policy version, and model governance state without a separate
    console. The historical per-request AUDIT trail lives in the ZeroShield
    dashboard and is joined by `request_id` (the incident id shown per request).
    """
    import httpx
    r = client.get("/observability", cast_to=httpx.Response)
    try:
        return r.json()
    except Exception:
        return {"error": "observability_unavailable", "status": r.status_code}


RAG_COLLECTION = os.environ.get("RAG_COLLECTION", "zeroshield-rag-e2e")


def rag_ingest(client: OpenAI, name: str, content: str, collection: str | None = None) -> dict:
    """Ingest into the PLATFORM vector firewall (per-org Pinecone store + ingest-time
    poison/PII scan) via the gateway's /v1/rag/ingest — through the SAME OpenAI client
    transport. No local store, no provider SDK."""
    import httpx
    coll = collection or RAG_COLLECTION
    body = {"collection": coll, "documents": [{"text": content, "metadata": {"name": name}}]}
    try:
        r = client.post("/rag/ingest", cast_to=httpx.Response, body=body)
    except APIStatusError as e:
        b = _error_body(e)
        return {"ok": False, "collection": coll, "status": e.status_code,
                "message": b.get("message"), "code": b.get("code")}
    try:
        data = r.json()
    except Exception:
        data = {}
    return {"ok": True, "collection": coll, "status": getattr(r, "status_code", 200), **data}


def rag_query(client: OpenAI, query: str, collection: str | None = None, n_results: int = 4) -> dict:
    """Retrieve from the platform vector store WITH grounding / poison-scan via the
    gateway's /v1/rag/query (NOT a client-side index)."""
    import httpx
    coll = collection or RAG_COLLECTION
    body = {"collection": coll, "query": query, "n_results": n_results}
    try:
        r = client.post("/rag/query", cast_to=httpx.Response, body=body)
    except APIStatusError as e:
        b = _error_body(e)
        return {"ok": False, "collection": coll, "status": e.status_code,
                "message": b.get("message"), "code": b.get("code"), "documents": []}
    try:
        return {"ok": True, "collection": coll, **r.json()}
    except Exception:
        return {"ok": False, "collection": coll, "documents": []}

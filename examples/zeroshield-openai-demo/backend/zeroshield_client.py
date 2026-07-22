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

try:
    from config import GATEWAY_BASE_URL as _CFG_BASE
except Exception:  # pragma: no cover
    _CFG_BASE = os.environ.get("ZEROSHIELD_BASE_URL", "https://aimeshgateway.zeroshield.ai/v1")

GATEWAY_BASE_URL = (_CFG_BASE or "").rstrip("/") or "https://aimeshgateway.zeroshield.ai/v1"
# Optional process-wide fallback (local CLI / legacy env). Prefer per-request keys.
GATEWAY_API_KEY = os.environ.get("ZEROSHIELD_API_KEY", "")


def make_client(api_key: str | None = None, base_url: str | None = None) -> OpenAI:
    """The ONE place an OpenAI client is constructed — drop-in, two fields."""
    key = (api_key or GATEWAY_API_KEY or "").strip()
    url = (base_url or GATEWAY_BASE_URL or "").rstrip("/")
    if not key:
        raise RuntimeError("gateway API key is not set (login required)")
    if not url:
        raise RuntimeError("ZEROSHIELD_BASE_URL is not set")
    return OpenAI(api_key=key, base_url=url, timeout=90, max_retries=1)


def _zs(d: dict) -> dict:
    return d.get("zeroshield") or {}


# Governance signals the gateway puts on the RESPONSE HEADERS rather than the body.
# Read via the stock SDK's `.with_raw_response` accessor (same call, no extra request).
# Only headers the gateway actually sent are reported — an absent header means the
# gateway did not send it, and the UI must not imply otherwise.
_GOV_HEADER_PREFIXES = ("x-ratelimit-", "x-zeroshield-")
# Routing headers are already rendered from the pipeline trace; repeating them in the
# governance strip would be noise.
_GOV_HEADER_SKIP = {
    "x-zeroshield-original-model",
    "x-zeroshield-routed-model",
    "x-zeroshield-routing-source",
    "x-zeroshield-rerouted",
    "x-zeroshield-routing-reason",
    "x-zeroshield-routing-policy-summary",
}


def _gov_headers(raw: Any) -> dict:
    """Governance/quota headers off the raw HTTP response, lowercased.

    Notable members, each present only under its own condition:
      * ``x-ratelimit-limit-tokens`` / ``-remaining-tokens`` / ``-reset-tokens`` —
        emitted only when the key carries a NON-ZERO ceiling, so a key with no
        configured ceiling legitimately shows none.
      * ``x-zeroshield-clamped`` — request params the gateway silently rewrote
        (e.g. ``n=5->1``, ``max_tokens=9999->4096``).
      * ``x-zeroshield-review-required`` — the operator configured ``human_review``
        for a detector that fired; the verdict normalises to ``flag``.
    """
    try:
        headers = getattr(raw, "headers", None) or {}
        items = headers.items()
    except Exception:
        return {}
    out = {}
    for k, v in items:
        lk = str(k).lower()
        if lk in _GOV_HEADER_SKIP:
            continue
        if lk.startswith(_GOV_HEADER_PREFIXES):
            out[lk] = v
    return out


def _error_body(e: APIStatusError) -> dict:
    """The gateway's firewall-block envelope (carries pipeline_trace + verdict).

    Prefer the FULL HTTP response body. The OpenAI SDK sets ``e.body`` to the
    *nested* ``error`` object only (``{message,type,param,code}``), which drops
    ZeroShield top-level fields: ``pipeline_trace``, ``category``, ``blocked_by``,
    ``request_id``, and the original ``code``.
    """
    try:
        full = json.loads(e.response.text)
        if isinstance(full, dict):
            return full
    except Exception:
        pass
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        return body
    return {"message": str(e)}


# Why the gateway refused, by OpenAI error code. Anything unlisted is a security
# verdict — the safe default, since an unrecognised refusal from a firewall should
# not be presented to the reader as a benign limit.
_REFUSAL_KINDS = {
    "context_length_exceeded": "size",
    "rate_limit_exceeded": "quota",
    "content_filter": "security",
}

_REFUSAL_LABELS = {
    "size": "Request too large — rejected on size, not on content.",
    "quota": "Rate/quota ceiling reached — not a security verdict.",
    "security": "Blocked by security policy.",
}


def _blocked_result(e: APIStatusError) -> dict:
    """Turn a firewall block (403/422/503) into a structured result with the trace,
    so the demo VISUALIZES the block instead of erroring out."""
    body = _error_body(e)
    nested = body.get("error") if isinstance(body.get("error"), dict) else {}
    message = (
        body.get("message")
        or nested.get("message")
        or str(e)
    )
    # Prefer org threat category; fall back to OpenAI content_filter / ZS code.
    threat = (
        body.get("category")
        or nested.get("code")
        or body.get("code")
    )
    pt = body.get("pipeline_trace") if isinstance(body.get("pipeline_trace"), dict) else {}
    synth = {
        "zeroshield": {
            "request_id": body.get("request_id") or nested.get("request_id"),
            "action": "block",
            "threat_type": threat,
            "detail": message,
            "guard_reason": message,
            "blocked_by": body.get("blocked_by"),
            "detection_tier": body.get("detection_tier"),
            "processing_time_ms": pt.get("total_latency_ms"),
            "routing": pt.get("routing") or {},
        },
        "pipeline_trace": pt,
    }
    # Not every non-2xx is a SECURITY verdict. A size rejection reports
    # `context_length_exceeded` and a quota rejection `rate_limit_exceeded`; calling
    # either "blocked by the firewall" would overstate what the firewall did.
    err_code = str(nested.get("code") or body.get("code") or "")
    kind = _REFUSAL_KINDS.get(err_code, "security")
    return {
        "content": "",
        "model": None,
        "blocked": True,
        "refusal_kind": kind,
        "error_code": err_code,
        "status": e.status_code,
        "message": message,
        "category": body.get("category") or threat,
        "trace": summarize_trace(synth),
        "governance_headers": _gov_headers(getattr(e, "response", None)),
        "raw": body,
    }


def _passthrough_stage(stage: Any) -> dict:
    """Copy a gateway stage object verbatim; normalize name from stage if needed."""
    if not isinstance(stage, dict):
        return {}
    out = dict(stage)
    if not out.get("name") and out.get("stage"):
        out["name"] = out["stage"]
    return out


def summarize_trace(body: dict) -> dict:
    """Normalize the gateway envelope for the demo pipeline visualizer.

    Stages and the pipeline_trace root are passed through so the UI can render
    the same StageTimeline / LogDetail fields as the main console (transparency
    keys, routing adjudicator fields, I/O, latency breakdown).
    """
    zs = _zs(body)
    raw_trace = body.get("pipeline_trace") or {}
    if not isinstance(raw_trace, dict):
        raw_trace = {}

    stages = [_passthrough_stage(s) for s in (raw_trace.get("stages") or []) if s]

    # Full trace root for I/O + latency (PIPELINE-0015/0017/0021/0022).
    pipeline_trace = dict(raw_trace)
    pipeline_trace["stages"] = stages

    # Prefer zeroshield.routing, then trace-root routing, then model_routing stage.
    zs_routing = zs.get("routing") if isinstance(zs.get("routing"), dict) else {}
    root_routing = raw_trace.get("routing") if isinstance(raw_trace.get("routing"), dict) else {}
    mr_stage = next((s for s in stages if s.get("name") == "model_routing"), None) or {}
    routing_src = zs_routing or root_routing or mr_stage

    requested = (
        routing_src.get("original_model")
        or routing_src.get("requested_model")
        or ""
    )
    selected = (
        routing_src.get("selected_model")
        or routing_src.get("routed_model")
        or ""
    )
    compact_routing = {
        "requested": requested or "auto",
        "selected": selected,
        "rerouted": routing_src.get("rerouted"),
        "reason": routing_src.get("routing_reason") or routing_src.get("reroute_reason") or "",
        "decision_source": routing_src.get("decision_source") or "",
        "fallback_reason_code": routing_src.get("fallback_reason_code"),
        "weights": routing_src.get("weights"),
        "compliance": routing_src.get("compliance_requirements"),
        "data_sensitivity": routing_src.get("data_sensitivity"),
        # Full adjudicator fields for RoutingDecisionCard parity.
        "requested_model": requested or routing_src.get("requested_model") or "auto",
        "selected_model": selected or routing_src.get("selected_model") or "",
        "routed_model": routing_src.get("routed_model") or selected or "",
        "route_destination": routing_src.get("route_destination") or "llm",
        "route_destination_label": routing_src.get("route_destination_label") or "",
        "routing_reason": routing_src.get("routing_reason") or routing_src.get("reroute_reason") or "",
        "decision_source_label": routing_src.get("decision_source_label") or "",
        "policy_summary": routing_src.get("policy_summary") or "",
        "decision_factors": routing_src.get("decision_factors") or [],
        "routing_score": routing_src.get("routing_score") or 0,
        "candidate_count": routing_src.get("candidate_count") or 0,
        "fallback_chain": routing_src.get("fallback_chain") or [],
        "evaluator_model": routing_src.get("evaluator_model") or "",
    }
    if compact_routing and not pipeline_trace.get("routing"):
        pipeline_trace["routing"] = {
            k: compact_routing[k]
            for k in (
                "requested_model", "selected_model", "routed_model",
                "route_destination", "route_destination_label", "routing_reason",
                "decision_source", "decision_source_label", "policy_summary",
                "decision_factors", "weights", "routing_score", "candidate_count",
                "fallback_chain", "evaluator_model",
            )
            if compact_routing.get(k) not in (None, "", [], {})
        }

    processing_ms = zs.get("processing_time_ms")
    if processing_ms is None:
        processing_ms = raw_trace.get("total_latency_ms")

    return {
        "request_id": zs.get("request_id") or raw_trace.get("request_id"),
        "action": zs.get("action") or raw_trace.get("final_action"),
        "threat_type": zs.get("threat_type"),
        "confidence": zs.get("confidence"),
        "matched_patterns": zs.get("matched_patterns") or [],
        "processing_time_ms": processing_ms,
        "guard_action": zs.get("guard_action"),
        "guard_reason": zs.get("guard_reason") or zs.get("detail") or "",
        "detail": zs.get("detail") or zs.get("guard_reason") or "",
        "blocked_by": zs.get("blocked_by") or "",
        "routing": compact_routing,
        "stages": stages,
        "pipeline_trace": pipeline_trace,
        "final_action": raw_trace.get("final_action") or zs.get("action"),
        "total_latency_ms": raw_trace.get("total_latency_ms"),
        "ttft_ms": raw_trace.get("ttft_ms"),
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
    return {"content": content, "model": body.get("model"), "trace": summarize_trace(body),
            "governance_headers": _gov_headers(raw), "raw": body}


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
                "status": r["status"], "message": r["message"],
                "refusal_kind": r["refusal_kind"], "error_code": r["error_code"],
                "governance_headers": r["governance_headers"], "raw": r["raw"]}
    body = json.loads(raw.text)
    return {"output_text": body.get("output_text", ""), "model": body.get("model"),
            "trace": summarize_trace(body), "governance_headers": _gov_headers(raw), "raw": body}


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

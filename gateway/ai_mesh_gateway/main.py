#!/usr/bin/env python3
"""
AIGuardX Gateway Proxy.
Sits between clients and LLM; calls backend policy check and optional security scan;
registers as gateway agent and reports stats for SOC.
"""
import asyncio
import base64
import json
import logging
import os
import platform
import re
import sys
import time
import uvicorn
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse


try:
    from .config import load_config
    from .context_assembler import minimize_context
    from .jobs import enqueue_job
    from .policy_signing import signing_enforced, _get_signing_key
    from .telemetry_ops import (
        emit_operational_event,
        EVENT_CLASS_POLICY_HMAC_MISCONFIG,
    )
    from .bedrock_tier2_breaker import Tier2UnavailableStrict
except ImportError:
    from config import load_config
    from context_assembler import minimize_context
    from jobs import enqueue_job
    from policy_signing import signing_enforced, _get_signing_key
    from telemetry_ops import (
        emit_operational_event,
        EVENT_CLASS_POLICY_HMAC_MISCONFIG,
    )
    from bedrock_tier2_breaker import Tier2UnavailableStrict  # type: ignore[no-redef]
from ai_mesh_shared.openai_request_normalizer import normalize_openai_chat_request

_gateway_public_url = (os.environ.get("GATEWAY_PUBLIC_URL", "") or "").strip().rstrip("/")
_backend_public_url = (os.environ.get("BACKEND_PUBLIC_URL", "") or "").strip().rstrip("/")
_backend_docs_url = f"{_backend_public_url}/docs/" if _backend_public_url else "/docs/"
_log_subscriber = None

LOG = logging.getLogger("gateway")
app = FastAPI(
    title="AIGuardX Gateway Proxy",
    description=(
        "## Data Plane — OpenAI-Compatible LLM Proxy\n\n"
        "The Gateway Proxy sits between client applications and LLM providers, "
        "enforcing security policies, performing content scanning, and routing "
        "requests via LiteLLM to 100+ LLM providers.\n\n"
        "### Authentication\n\n"
        "Requests require a **Gateway API Key** via `Authorization: Bearer <key>` header.\n\n"
        "Keys are created in the Control Plane (backend) and cached in Redis for "
        "zero-latency lookups. Each key carries project context, model allowlists, "
        "and rate limits.\n\n"
        "### How to Get a Gateway API Key\n\n"
        f"1. Go to the **Backend (Control Plane)** docs at `{_backend_docs_url}`\n"
        "2. Use `POST /api/auth/token/` to get a JWT token\n"
        "3. Use `POST /api/gateways/keys/` with the JWT token to create a Gateway API Key\n"
        "4. Copy the `key` field from the response (shown only once)\n"
        "5. Use it here as `Authorization: Bearer <key>`\n\n"
        "### How It Works\n\n"
        "1. Client sends an OpenAI-compatible request to `/v1/chat/completions`\n"
        "2. Gateway authenticates the request via Redis key lookup\n"
        "3. Prompt is sent to the backend for policy evaluation\n"
        "4. If allowed, request is forwarded to the configured LLM provider\n"
        "5. Response is optionally checked against policies before returning\n\n"
        "### Streaming\n\n"
        'Set `"stream": true` in the request body to receive Server-Sent Events (SSE).'
    ),
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    servers=[
        {"url": _gateway_public_url or "/", "description": "Gateway (Data Plane)"},
    ],
)

# ── Auth middleware (must be added before uvicorn starts) ──
_redis_url = os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0")
_auth_enabled = os.environ.get("GATEWAY_AUTH_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
_async_vector_ingest_enabled = os.environ.get("GATEWAY_ASYNC_VECTOR_INGEST", "true").lower() in ("true", "1", "yes")

if _auth_enabled:
    try:
        from .middleware import AuthMiddleware
    except ImportError:
        from middleware import AuthMiddleware

    app.add_middleware(AuthMiddleware, redis_url=_redis_url)

from starlette.middleware.cors import CORSMiddleware

# ── Prometheus observation middleware (Phase D) ──
# Records every request as an Counter+Histogram observation. Decision label
# distinguishes 2xx/3xx (allowed), 4xx (blocked), 5xx (error). Org label is
# resolved from auth_context if AuthMiddleware ran first, else "anonymous".
# Counters/gauges only, no PII in labels.
try:
    from .metrics import (
        inc_active_connections as _prom_inc_active,
        record_request as _prom_record_request,
    )
except ImportError:
    from metrics import (  # type: ignore[no-redef]
        inc_active_connections as _prom_inc_active,
        record_request as _prom_record_request,
    )


@app.middleware("http")
async def _prom_observe(request, call_next):
    # Skip self-observation on the metrics endpoint to avoid recursion noise.
    if request.url.path == "/metrics":
        return await call_next(request)
    _prom_inc_active(1)
    _t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = getattr(response, "status_code", 200)
        return response
    finally:
        elapsed_s = time.perf_counter() - _t0
        _prom_inc_active(-1)
        auth_ctx = getattr(request.state, "auth_context", None)
        org = getattr(auth_ctx, "org_slug", "") if auth_ctx else ""
        if status >= 500:
            decision = "error"
        elif status >= 400:
            decision = "blocked"
        else:
            decision = "allowed"
        try:
            _prom_record_request(org, decision, elapsed_s)
        except Exception:
            pass


# SEC-01 FIX: Environment-based CORS origins instead of wildcard
def _split_csv_env(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


_cors_origins: list[str] = []
for _env_key in ("GATEWAY_CORS_ORIGINS", "FRONTEND_ORIGIN", "ASGI_ALLOWED_ORIGINS"):
    for _origin in _split_csv_env(os.environ.get(_env_key, "")):
        if _origin not in _cors_origins:
            _cors_origins.append(_origin)

if not _cors_origins:
    _cors_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8180",
        "http://127.0.0.1:8180",
        "http://localhost:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["content-type", "authorization", "x-user-id", "x-endpoint-id", "x-agent-data"],
    expose_headers=["content-type", "content-length", "x-request-id"],
)

from fastapi.responses import HTMLResponse


@app.get("/docs", include_in_schema=False)
async def scalar_docs():
    """Serve Scalar API Reference UI."""
    return HTMLResponse(
        """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AIGuardX Gateway API Docs</title>
    <style>body { margin: 0; }</style>
</head>
<body>
    <script
        id="api-reference"
        data-url="/openapi.json"
        data-configuration='{"theme": "kepler", "hideDownloadButton": false}'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.25.55"></script>
</body>
</html>"""
    )


# Set at startup
CONFIG = None
CONFIG_SYNC = None
AGENT_ID = None
LLM_ROUTER = None
POLICY_SYNC = None
RATE_LIMITER = None
INPUT_SCANNER = None
VECTOR_POLICY_SYNC = None
VECTOR_CLIENTS: dict[str, object] = {}
VECTOR_PROVIDER_SYNC = None
CONTEXT_GUARD = None
REDIS_CLIENT = None
TELEMETRY = None
OUTPUT_GUARD = None

CIRCUIT_BREAKER = None
RAG_PIPELINE = None
BEDROCK_EMBEDDER = None
GROUNDING_GUARD = None

# Gateway version for auto-update check (should match backend expectation)
GATEWAY_VERSION = "0.1.0"

# In-memory metrics
METRICS = {
    "total_requests": 0,
    "blocked": 0,
    "allowed": 0,
    "sum_latency_ms": 0.0,
    "active_connections": 0,
}


def _http_request(method, url, data=None, api_key=None):
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data else None, method=method
    )
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("X-Agent-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        LOG.exception("HTTP request failed")
        return None, str(e)

STARTUP_RETRY_MAX_ATTEMPTS = 5
STARTUP_RETRY_BASE_DELAY_SECONDS = 2.0
STARTUP_RETRY_MAX_DELAY_SECONDS = 30.0


def _http_request_with_retry(
    method: str,
    url: str,
    data: dict | None = None,
    api_key: str | None = None,
    max_attempts: int = STARTUP_RETRY_MAX_ATTEMPTS,
    base_delay: float = STARTUP_RETRY_BASE_DELAY_SECONDS,
    max_delay: float = STARTUP_RETRY_MAX_DELAY_SECONDS,
) -> tuple:
    import random

    last_code = None
    last_body = None

    for attempt in range(1, max_attempts + 1):
        code, body = _http_request(method, url, data=data, api_key=api_key)
        if code is not None:
            return code, body
        last_code = code
        last_body = body
        if attempt < max_attempts:
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            jitter = random.uniform(0, delay * 0.25)
            total_delay = delay + jitter
            LOG.warning(
                "Backend unreachable (attempt %d/%d). Retrying in %.1fs... [%s]",
                attempt, max_attempts, total_delay, url,
            )
            time.sleep(total_delay)

    LOG.error(
        "Backend unreachable after %d attempts. Gateway starting without registration. [%s]",
        max_attempts, url,
    )
    return last_code, last_body

def _parse_version(v):
    if not v:
        return (0, 0, 0)
    parts = []
    for s in str(v).strip().split(".")[:4]:
        try:
            parts.append(int(s))
        except ValueError:
            parts.append(0)
    return tuple(parts)


# ── SAFE_ERROR_CONTRACT: Client-safe error responses (BUG-1: Sensitive Data Leak Prevention) ──
# STRICT RULE: NEVER return raw prompts, secrets, PII patterns, or detection rules to clients.
# This is a CRITICAL SECURITY requirement.

_SCAN_THREAT_CATEGORIES = frozenset({
    "prompt_injection",
    "jailbreak",
    "goal_hijacking",
    "pii",
    "secret",
    "toxicity",
    "dos",
    "tool_overreach",
    "data_leakage",
    "rag_poisoning",
})


def _resolve_pipeline_blocked_by(
    *,
    code: str,
    threat_category: str,
    detection_tier: str = "",
) -> str:
    """Map a block to the pipeline stage that actually enforced it (not always policy)."""
    if code == "rate_limit_exceeded":
        return "rate_limit"
    if threat_category == "blocked_keyword":
        return "firewall_keywords"
    tier = (detection_tier or "").strip().lower()
    if tier in {"tier_1", "tier_1_5", "tier_2", "input_scan"}:
        return "input_scan"
    if tier == "policy":
        return "policy"
    if tier == "output_guard":
        return "output_guardrail"
    if tier == "threat_intel":
        return "policy"
    if threat_category in _SCAN_THREAT_CATEGORIES:
        return "input_scan"
    if threat_category in {"policy_violation", "compliance_violation"}:
        return "policy"
    if threat_category in {"high_risk_actor", "threat_intel"}:
        return "policy"
    return "input_scan"


def _build_safe_block_response(
    status_code: int,
    code: str,
    threat_category: str,
    request_id: str | None = None,
    internal_detail: str | None = None,
    detection_tier: str = "",
    pipeline_trace: dict | None = None,
) -> JSONResponse:
    """
    Build a client-safe error response for policy blocks.
    NEVER exposes:
      - raw prompts
      - detected secrets or PII
      - matched detection patterns/rules
      - specific threat details
    
    ONLY exposes:
      - generic policy violation message
      - request ID (for support reference)
      - threat category (generic)
      - action taken
    """
    import uuid
    _request_id = request_id or f"zs-{uuid.uuid4().hex[:12]}"
    
    # Safe category to message mapping (client-visible)
    safe_messages = {
        "prompt_injection": "Request blocked due to security policy",
        "jailbreak": "Request blocked due to security policy",
        "pii": "Request blocked due to security policy",
        "secret": "Request blocked due to security policy",
        "toxicity": "Request blocked due to security policy",
        "compliance_violation": "Request blocked due to compliance policy",
        "policy_violation": "Request blocked due to security policy",
        "threat_intel": "Request blocked: insufficient permissions",
        "mcp_injection": "Request blocked: invalid operation",
        "mcp_args_injection": "Request blocked: invalid operation",
        "model_not_allowed": "Model not in allowlist",
        "tier2_degraded": "Service temporarily unavailable",
    }
    
    message = safe_messages.get(threat_category, "Request blocked due to security policy")
    
    # Log internal details server-side ONLY (never in response)
    if internal_detail:
        LOG.warning(
            "[SECURITY_BLOCK] code=%s, category=%s, request_id=%s, detail=%s",
            code, threat_category, _request_id, internal_detail,
        )

    blocked_by = _resolve_pipeline_blocked_by(
        code=code,
        threat_category=threat_category,
        detection_tier=detection_tier,
    )
    content = {
        "error": "blocked",
        "message": message,
        "code": code,
        "request_id": _request_id,  # For support inquiries only
        "category": threat_category,  # Generic: NOT specific threat type
        "blocked_by": blocked_by,
        "detection_tier": detection_tier or "",
        "pipeline_stage": blocked_by,
    }
    if pipeline_trace:
        content["pipeline_trace"] = pipeline_trace
    return JSONResponse(status_code=status_code, content=content)


def _redact_for_client_response(zeroshield_dict: dict | None) -> dict | None:
    """
    Take internal zeroshield metadata and produce a client-safe version.
    REMOVES all potentially sensitive fields.
    """
    if not zeroshield_dict or not isinstance(zeroshield_dict, dict):
        return None
    
    # Operator dashboard fields (Module 1.1 simulator) — no raw prompts/responses.
    safe_fields = {
        "request_id",
        "action",
        "detection_tier",
        "processing_time_ms",
        "reason",
        "detail",
        "threat_type",
        "confidence",
        "matched_patterns",
        "routing_reason",
        "decision_source",
        "policy_summary",
        "decision_factors",
        "weights",
        "selected_model",
        "original_model",
        "routed_model",
        "rerouted",
        "routing",
        "reason_code",
        "guard_reason",
        "guard_action",
        "guard_model",
        "recommended_action",
        "guard_findings",
        "enforcement_source",
    }
    redacted = {k: v for k, v in zeroshield_dict.items() if k in safe_fields}
    
    # DO NOT include:
    # - reason (exposes why blocked)
    # - detail (exposes threat specifics)
    # - matched_patterns (exposes detection rules)
    # - compliance_tags (exposes compliance framework details)
    # - original_prompt_hash (can aid targeting)
    # - redacted_prompt / redacted_response (may still contain PII)
    # - threat_type (too specific)
    # - confidence (aids evasion)
    
    return redacted if redacted else None


def _check_version():
    """Optional: call version endpoint and log if update available or below min."""
    cfg = CONFIG
    if not cfg or not cfg.get("backend_url"):
        return
    url = f"{cfg['backend_url']}/api/agents/version/"
    code, body = _http_request_with_retry("GET", url, api_key=cfg.get("api_key"))
    if code != 200 or not isinstance(body, dict):
        return
    min_ver = body.get("min_version") or "0"
    rec_ver = body.get("recommended_version") or min_ver
    download_url = (body.get("download_url") or "").strip()
    current = _parse_version(GATEWAY_VERSION)
    min_t = _parse_version(min_ver)
    rec_t = _parse_version(rec_ver)
    if current < min_t:
        LOG.warning(
            "Gateway version %s is below required minimum %s. Consider updating.",
            GATEWAY_VERSION,
            min_ver,
        )
    elif rec_t > current and download_url:
        LOG.info("Gateway update available: %s. Download: %s", rec_ver, download_url)


def _register():
    global AGENT_ID
    cfg = CONFIG
    url = f"{cfg['backend_url']}/api/gateways/instances/register/"
    payload = {
        "agent_type": "gateway",
        "name": cfg["gateway_name"],
        "endpoint_identifier": cfg.get("gateway_location")
        or platform.node()
        or "gateway",
    }
    code, body = _http_request_with_retry("POST", url, data=payload, api_key=cfg.get("api_key"))
    if code in (200, 201) and body.get("agent_id"):
        AGENT_ID = body["agent_id"]
        LOG.info("Registered gateway agent_id=%s", AGENT_ID)
        return True
    LOG.error("Registration failed: %s %s", code, body)
    return False


def _policy_check(
    prompt,
    response_text="",
    user_id=None,
    endpoint_id=None,
    agent_data=None,
    project_id=None,
    risk_score=None,
    model=None,
    key_prefix=None,
    organization_id=None,
):
    cfg = CONFIG
    url = f"{cfg['backend_url']}/api/policy/check/"
    payload = {
        "prompt": prompt,
        "response": response_text or "",
        "agent_id": AGENT_ID,
        "user_id": user_id,
        "endpoint_id": endpoint_id,
    }
    if project_id is not None:
        payload["project_id"] = project_id
    if risk_score is not None:
        payload["risk_score"] = risk_score
    metadata = {}
    if model:
        metadata["model"] = str(model)
    if key_prefix:
        metadata["key_prefix"] = str(key_prefix)
    if organization_id is not None:
        metadata["organization_id"] = organization_id
    if metadata:
        payload["metadata"] = metadata
    if agent_data is not None and isinstance(agent_data, dict):
        payload["agent_data"] = agent_data
    return _http_request_with_retry("POST", url, data=payload, api_key=cfg.get("api_key"))


def _policy_check_cached(
    prompt,
    response_text="",
    user_id=None,
    endpoint_id=None,
    agent_data=None,
    project_id=None,
    risk_score=None,
    model=None,
    org_slug="default",
    key_prefix="",
    organization_id=None,
):
    """
    Evaluate prompt/response against cached policies locally.
    Falls back to HTTP _policy_check() if cache is not loaded.

    Returns (status_code, response_dict) matching the backend
    /api/policy/check/ response shape.
    """
    if POLICY_SYNC is None or not POLICY_SYNC.is_loaded:
        if CONFIG.get("policy_cache_require_loaded", True):
            LOG.warning("Policy cache unavailable; fail-closed policy check")
            return 503, {
                "action": "block",
                "matched_policies": [],
                "matched_rules": [],
                "matched_policy_names": [],
                "matched_policy_severities": [],
                "matched_policy_categories": [],
                "matched_rule_descriptions": [],
                "message": "Policy cache unavailable. Request blocked by fail-closed policy.",
            }
        LOG.debug("Policy cache not loaded, falling back to backend HTTP")
        return _policy_check(
            prompt, response_text, user_id, endpoint_id,
            agent_data, project_id, risk_score, model,
            key_prefix=key_prefix,
            organization_id=organization_id,
        )

    from policy_engine import evaluate, apply_redaction

    result = evaluate(
        prompt=prompt,
        response_text=response_text,
        compiled_policies=POLICY_SYNC.get_policies(org_slug),
    )

    response = {
        "action": result.action,
        "matched_policies": result.matched_policy_codes,
        "matched_rules": result.matched_rule_names,
        "matched_policy_names": result.matched_policy_names,
        "matched_policy_severities": result.matched_policy_severities,
        "matched_policy_categories": result.matched_policy_categories,
        "matched_rule_descriptions": result.matched_rule_descriptions,
        "message": result.message,
    }

    if result.action == "redact" and result.redaction_hints:
        if prompt:
            response["redacted_prompt"] = apply_redaction(prompt, result.redaction_hints)
        if response_text:
            response["redacted_response"] = apply_redaction(response_text, result.redaction_hints)

    return 200, response


def _security_scan(prompt, response_text=""):
    cfg = CONFIG
    url = f"{cfg['backend_url']}/api/security/scan/"
    payload = {"prompt": prompt, "response": response_text or ""}
    return _http_request_with_retry("POST", url, data=payload, api_key=cfg.get("api_key"))


def _extract_prompt_from_messages(messages):
    """Build a single prompt string from OpenAI-style messages."""
    parts = []
    for m in messages or []:
        role = m.get("role", "")
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                c.get("text", str(c)) for c in content if isinstance(c, dict)
            )
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _extract_agent_data(body: dict, x_agent_data: str | None):
    """agent_data from body.agent_data or X-Agent-Data header (base64 JSON)."""
    if body and isinstance(body.get("agent_data"), dict):
        return body["agent_data"]
    if x_agent_data:
        try:
            decoded = base64.b64decode(x_agent_data).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            pass
    return None


def _extract_response_from_completion(completion):
    """Extract assistant response text from chat completion (non-streaming)."""
    choices = completion.get("choices") or []
    if not choices:
        return ""
    c = choices[0]
    msg = c.get("message") or c.get("delta") or {}
    return msg.get("content") or ""


import hashlib as _hashlib
import uuid as _uuid

try:
    from .patterns import get_compliance_tags as _get_compliance_tags
except ImportError:
    try:
        from patterns import get_compliance_tags as _get_compliance_tags
    except ImportError:
        def _get_compliance_tags(pattern_keys: list[str]) -> list[str]:
            return (CONFIG or {}).get("compliance_frameworks", [])


def _build_zeroshield_metadata(
    action: str,
    reason: str,
    detection_tier: str = "none",
    threat_type: str = "none",
    confidence: float = 0.0,
    matched_patterns: list[str] | None = None,
    compliance_tags: list[str] | None = None,
    original_prompt: str = "",
    redacted_prompt: str | None = None,
    redacted_response: str | None = None,
    rewritten_response: str | None = None,
    detail: str | None = None,
    processing_time_ms: float = 0.0,
    intent: str = "",
    review_required: bool = False,
    security_incident: bool = False,
    factuality_warning: bool = False,
    routing: dict | None = None,
) -> dict:
    """Build the unified zeroshield metadata dict for any response."""
    resolved_patterns = matched_patterns or []
    resolved_tags = compliance_tags if compliance_tags is not None else _get_compliance_tags(resolved_patterns)

    result = {
        "request_id": f"zs-{_uuid.uuid4().hex[:12]}",
        "action": action,
        "reason": reason,
        "detail": detail or reason,
        "detection_tier": detection_tier or "none",
        "threat_type": threat_type or "none",
        "confidence": round(confidence, 4),
        "matched_patterns": resolved_patterns,
        "compliance_tags": resolved_tags,
        "original_prompt_hash": f"sha256:{_hashlib.sha256(original_prompt.encode('utf-8')).hexdigest()[:16]}" if original_prompt else "",
        "redacted_prompt": redacted_prompt,
        "redacted_response": redacted_response,
        "rewritten_response": rewritten_response,
        "review_required": review_required,
        "security_incident": security_incident,
        "factuality_warning": factuality_warning,
        "processing_time_ms": round(processing_time_ms, 2),
    }
    if intent:
        result["intent"] = intent
    if routing:
        result["routing"] = routing
    return result


def _estimate_request_tokens(prompt: str, max_tokens: int = 0) -> int:
    """Rough token estimate for TPM pre-check (matches simulator: ~4 chars per token)."""
    prompt_tokens = max(1, len(prompt or "") // 4)
    completion_budget = max(0, int(max_tokens or 0))
    return max(1, prompt_tokens + completion_budget)


def _blocked_keyword_matches(prompt_lower: str, keyword: str) -> bool:
    """Match firewall blocked keywords on word boundaries (avoids substring false positives)."""
    kw = (keyword or "").strip().lower()
    if not kw or not prompt_lower:
        return False
    if re.search(r"\s", kw):
        return kw in prompt_lower
    return bool(re.search(rf"\b{re.escape(kw)}\b", prompt_lower))


def _coerce_string_list(*values) -> list[str]:
    items: list[str] = []

    def append_value(value) -> None:
        if value is None:
            return
        if isinstance(value, str):
            for raw_part in value.split(","):
                text = raw_part.strip()
                if text and text not in items:
                    items.append(text)
            return
        text = str(value).strip()
        if text and text not in items:
            items.append(text)

    for value in values:
        if isinstance(value, list):
            for item in value:
                append_value(item)
        else:
            append_value(value)
    return items


def _extract_chat_routing_preferences(body: dict, org_config: dict, auth_ctx, scan_verdict) -> dict:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    routing_preferences = body.get("routing_preferences") if isinstance(body.get("routing_preferences"), dict) else {}
    org_routing_enabled = bool(org_config.get("routing_enabled", True))
    routing_override = routing_preferences.get("enable_routing")
    if routing_override is None:
        routing_override = routing_preferences.get("routing_enabled")
    if routing_override is None:
        routing_override = metadata.get("enable_routing")
    if routing_override is None:
        routing_override = body.get("enable_routing")
    routing_override = routing_override if isinstance(routing_override, bool) else None
    routing_enabled = routing_override if routing_override is not None else org_routing_enabled
    preferred_model = str(body.get("model") or "auto")
    required_compliance = _coerce_string_list(
        routing_preferences.get("compliance_requirements"),
        body.get("compliance_requirements"),
        metadata.get("compliance_requirements"),
    )
    data_sensitivity = (
        routing_preferences.get("data_sensitivity")
        or metadata.get("data_sensitivity")
        or body.get("data_sensitivity")
        or "public"
    )
    latency_budget_ms = int(
        routing_preferences.get("latency_budget_ms")
        or body.get("latency_budget_ms")
        or metadata.get("latency_budget_ms")
        or 30000
    )
    max_tokens = int(body.get("max_tokens") or 0)
    estimated_tokens = LLM_ROUTER.estimate_prompt_tokens(body.get("messages") or [], max_tokens=max_tokens)
    weights = {
        "risk": float(routing_preferences.get("risk_weight") or org_config.get("routing_risk_weight", CONFIG.get("routing_risk_weight", 0.30))),
        "cost": float(routing_preferences.get("cost_weight") or org_config.get("routing_cost_weight", CONFIG.get("routing_cost_weight", 0.20))),
        "latency": float(routing_preferences.get("latency_weight") or org_config.get("routing_latency_weight", CONFIG.get("routing_latency_weight", 0.20))),
        "priority": float(routing_preferences.get("priority_weight") or org_config.get("routing_priority_weight", CONFIG.get("routing_priority_weight", 0.30))),
    }
    request_risk_score = float(
        routing_preferences.get("model_risk_score")
        or metadata.get("model_risk_score")
        or getattr(scan_verdict, "confidence", 0.0)
        or getattr(auth_ctx, "risk_score", 0.0)
        or 0.0
    )
    token_budget_tpm = getattr(auth_ctx, "rate_limit_tpm", None) if auth_ctx is not None else None
    return {
        "routing_enabled": routing_enabled,
        "routing_override": routing_override,
        "org_routing_enabled": org_routing_enabled,
        "preferred_model": preferred_model,
        "required_compliance": required_compliance,
        "data_sensitivity": data_sensitivity,
        "latency_budget_ms": latency_budget_ms,
        "estimated_tokens": estimated_tokens,
        "weights": weights,
        "request_risk_score": request_risk_score,
        "token_budget_tpm": token_budget_tpm,
    }


def _build_routing_metadata(
    selection,
    routing_enabled: bool,
    routing_override: bool | None,
    org_routing_enabled: bool,
    latency_budget_ms: int,
    data_sensitivity: str,
    required_compliance: list[str],
    weights: dict[str, float],
    request_risk_score: float,
    estimated_tokens: int,
    token_budget_tpm: int | None,
) -> dict:
    original_model = selection.requested_model or "auto"
    rerouted = bool(
        selection.model_name
        and original_model not in ("", "auto")
        and selection.model_name != original_model
    )
    return {
        "original_model": original_model,
        "selected_model": selection.model_name,
        "routed_model": selection.model_name,
        "routing_enabled": routing_enabled,
        "routing_override": routing_override,
        "org_routing_enabled": org_routing_enabled,
        "routed_model_id": selection.model_id,
        "routing_score": selection.score,
        "rerouted": rerouted,
        "reroute_reason": selection.reason,
        "routing_reason": selection.reason,
        "fallback_chain": selection.fallback_chain,
        "decision_source": selection.decision_source,
        "policy_summary": selection.policy_summary,
        "decision_factors": selection.decision_factors,
        "candidate_count": selection.candidate_count,
        "data_sensitivity": data_sensitivity,
        "compliance_requirements": required_compliance,
        "request_risk_score": request_risk_score,
        "estimated_tokens": estimated_tokens,
        "token_budget_tpm": token_budget_tpm,
        "latency_budget_ms": latency_budget_ms,
        "weights": weights,
    }


def _isolation_reroute_context(
    org_slug: str,
    routing_models: list,
    routing_prefs: dict,
    allowed_models: list | None,
    body: dict | None = None,
) -> dict:
    """Shared context for compliant kill-switch / model-state reroutes."""
    fallback_chains = CONFIG_SYNC.get_fallback_chains(org_slug) if CONFIG_SYNC else {}
    allowed_set = None
    if allowed_models:
        allowed_set = {str(m).lower() for m in allowed_models if m}
    body = body if isinstance(body, dict) else {}
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    data_sensitivity = (
        routing_prefs.get("data_sensitivity")
        or body.get("data_sensitivity")
        or metadata.get("data_sensitivity")
        or "public"
    )
    required_compliance = _coerce_string_list(
        routing_prefs.get("required_compliance"),
        body.get("compliance_requirements"),
        metadata.get("compliance_requirements"),
    )
    return {
        "routing_models": routing_models,
        "fallback_chains": fallback_chains,
        "data_sensitivity": data_sensitivity,
        "required_compliance": required_compliance,
        "allowed_set": allowed_set,
    }


def _apply_compliant_isolation_reroute(
    *,
    primary_model: str,
    requested_fallback: str,
    ctx: dict,
    scope: str,
    trigger_source: str,
    reason: str,
) -> tuple[str | None, dict]:
    """
    Resolve reroute target under the same hard eligibility filters as Stage-2.

    Returns (selected_model_or_none, audit_metadata). None => disable-first block.
    """
    try:
        from routing_isolation import (
            build_isolation_audit_metadata,
            resolve_compliant_fallback,
        )
    except ImportError:
        from .routing_isolation import (
            build_isolation_audit_metadata,
            resolve_compliant_fallback,
        )

    compliant, reason_code = resolve_compliant_fallback(
        primary_model=primary_model,
        requested_fallback=requested_fallback,
        routing_models=ctx["routing_models"],
        fallback_chains=ctx["fallback_chains"],
        data_sensitivity=ctx["data_sensitivity"],
        required_compliance=ctx["required_compliance"],
        allowed_models=ctx["allowed_set"],
    )
    chains = ctx.get("fallback_chains") or {}
    audit_meta = build_isolation_audit_metadata(
        event_kind="isolation_reroute",
        scope=scope,
        trigger_source=trigger_source,
        action="reroute" if compliant else "block",
        reason=reason or reason_code,
        original_model=primary_model,
        selected_model=compliant or "",
        data_sensitivity=ctx["data_sensitivity"],
        compliance_tags=ctx["required_compliance"],
        fallback_chain_version=chains.get("version"),
        fallback_reason_code=reason_code,
    )
    return compliant, audit_meta


try:
    from .stream_orchestration import (
        StreamFinalizeHooks,
        StreamLaunchContext,
        StreamRunMetrics,
        StreamScanMode,
        build_base_stream_headers,
        enrich_stream_headers,
        resolve_scan_mode,
        stream_with_finalize,
        streaming_preflight_block_body,
        wrap_secure_stream_if_needed,
    )
    from .metrics import record_stream_complete as _prom_record_stream_complete
    from .metrics import record_stream_guard_action as _prom_record_stream_guard
except ImportError:
    from stream_orchestration import (
        StreamFinalizeHooks,
        StreamLaunchContext,
        StreamRunMetrics,
        StreamScanMode,
        build_base_stream_headers,
        enrich_stream_headers,
        resolve_scan_mode,
        stream_with_finalize,
        streaming_preflight_block_body,
        wrap_secure_stream_if_needed,
    )
    from metrics import record_stream_complete as _prom_record_stream_complete
    from metrics import record_stream_guard_action as _prom_record_stream_guard


def _stream_preflight_block_if_needed(org_config: dict) -> JSONResponse | None:
    """Fail-closed JSON response before SSE when streaming preflight cannot complete."""
    merged = {**CONFIG, **(org_config or {})}
    block_body = streaming_preflight_block_body(
        merged,
        policy_sync_loaded=POLICY_SYNC is not None and POLICY_SYNC.is_loaded,
    )
    if block_body is None:
        return None
    METRICS["blocked"] += 1
    return JSONResponse(status_code=503, content=block_body)


def _stream_finalize_hooks() -> StreamFinalizeHooks:
    def _record_stream(org_slug: str, model: str, decision: str, metrics: StreamRunMetrics, elapsed_ms: float, **_kw):
        _prom_record_stream_complete(
            org_slug,
            model,
            decision,
            ttft_seconds=metrics.ttft_ms / 1000.0 if metrics.ttft_ms else 0.0,
            duration_seconds=elapsed_ms / 1000.0,
            had_error=metrics.had_error,
            fallback_before_token=metrics.fallback_before_first_token,
        )

    return StreamFinalizeHooks(
        circuit_breaker=CIRCUIT_BREAKER,
        rate_limiter=RATE_LIMITER,
        record_stream_complete=_record_stream,
        emit_telemetry=_emit_telemetry,
    )


def _launch_chat_stream_response(
    *,
    request: Request,
    body: dict,
    org_config: dict,
    org_slug: str,
    auth_ctx,
    redacted_prompt: str | None,
    route_selection=None,
    scan_verdict=None,
    user_id=None,
    project_id: str = "",
    key_hash: str = "",
    rate_limit_tpm: int = 0,
    estimated_tokens: int = 20,
    org_tpm_limit: int = 0,
    secure_output_scan: bool = True,
):
    """
    stream_phase + finalization_phase for /v1/chat/completions (SSE).

    Assumes eligibility_phase and selection_phase already ran in proxy_chat.
    """
    scan_mode = resolve_scan_mode(
        output_scan_enabled=bool(org_config.get("output_scan_enabled", CONFIG.get("output_scan_enabled", True))),
        input_scanner=INPUT_SCANNER if secure_output_scan else None,
        output_guard=OUTPUT_GUARD if secure_output_scan else None,
    )
    request_id = request.headers.get("X-Request-ID", f"zs-stream-{_uuid.uuid4().hex[:12]}")
    model = body.get("model", "")
    enforcement_mode = str(org_config.get("enforcement_mode", CONFIG.get("enforcement_mode", "block")))
    ctx = StreamLaunchContext(
        body=body,
        redacted_prompt=redacted_prompt,
        org_slug=org_slug or "default",
        model=model,
        key_hash=key_hash,
        rate_limit_tpm=rate_limit_tpm or 0,
        estimated_tokens=estimated_tokens,
        org_tpm_limit=org_tpm_limit or 0,
        route_selection=route_selection,
        scan_verdict=scan_verdict,
        scan_mode=scan_mode,
        request_id=request_id,
        user_id=user_id,
        project_id=str(project_id or ""),
        organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
        source_ip=request.client.host if request.client else "",
    )
    headers = enrich_stream_headers(
        build_base_stream_headers(),
        route_selection=route_selection,
        scan_verdict=scan_verdict,
        redacted_prompt=redacted_prompt,
        scan_mode=scan_mode,
        emit_debug=bool(org_config.get("stream_emit_debug_headers", CONFIG.get("stream_emit_debug_headers", False))),
    )
    stream_metrics = StreamRunMetrics()

    async def _provider_stream():
        async for chunk in LLM_ROUTER.acompletion_stream(body, redacted_prompt, metrics=stream_metrics):
            yield chunk

    inner = _provider_stream()
    if scan_mode != StreamScanMode.NONE and INPUT_SCANNER is not None and CONFIG.get("output_scan_enabled", True):
        inner = wrap_secure_stream_if_needed(
            inner,
            scan_mode=scan_mode,
            config={**CONFIG, **org_config},
            input_scanner=INPUT_SCANNER,
            output_guard=OUTPUT_GUARD,
            telemetry=TELEMETRY,
            ctx=ctx,
            record_guard_metric=_prom_record_stream_guard,
            stream_metrics=stream_metrics,
            enforcement_mode=enforcement_mode,
        )

    finalized = stream_with_finalize(
        inner,
        ctx,
        _stream_finalize_hooks(),
        decision="allowed",
        metrics=stream_metrics,
        finalize_timeout_ms=int(
            org_config.get("stream_finalize_timeout_ms", CONFIG.get("stream_finalize_timeout_ms", 5000))
        ),
    )
    return StreamingResponse(
        finalized,
        media_type="text/event-stream",
        headers=headers,
    )


_ROUTING_SENTINEL_MODELS = frozenset({"", "auto"})


def _is_routing_sentinel_model(model: str) -> bool:
    """Client hint meaning 'pick via router' — not an allowlist entry."""
    return (model or "").strip().lower() in _ROUTING_SENTINEL_MODELS


def _resolve_routing_hint_model(
    requested_model: str,
    org_config: dict,
    inference_models: list[dict] | None,
) -> str:
    """Map auto/empty to org default_model or first connected inference model."""
    current = (requested_model or "").strip()
    if not _is_routing_sentinel_model(current):
        return current
    default = str(org_config.get("default_model") or "").strip()
    if default and not _is_routing_sentinel_model(default):
        return default
    for entry in inference_models or []:
        name = str(entry.get("model_name") or "").strip()
        if name and not _is_guard_only_model(entry):
            return name
    return current


def _guard_model_names() -> frozenset[str]:
    """Models reserved for ZeroShield input/output scanning — never chat inference."""
    names = {
        os.getenv("ZEROSHIELD_GUARD_MODEL_NAME", "zeroshield-guard-120b").strip().lower(),
        "zeroshield-guard-120b",
        "bedrock-gpt-oss-120b-long-context",
    }
    return frozenset(n for n in names if n)


def _is_guard_only_model(model: dict) -> bool:
    name = str(model.get("model_name") or "").strip().lower()
    if name in _guard_model_names():
        return True
    provider = str(model.get("provider") or "").strip().lower()
    return provider == "internal"


def _filter_inference_eligible_models(routing_models: list[dict] | None) -> list[dict]:
    """Org-connected customer models only — not ZeroShield guard / internal scan models."""
    models = routing_models or []
    eligible: list[dict] = []
    for model in models:
        if _is_guard_only_model(model):
            continue
        if not model.get("is_active", True):
            continue
        if not (model.get("model_name") or model.get("model_id")):
            continue
        provider = str(model.get("provider") or "").strip().lower()
        api_key_set = bool(model.get("api_key_set"))
        # Organization-owned inference must carry tenant credentials.
        # Local/self-hosted ollama and Bedrock (gateway AWS env) can run without encrypted BYOK.
        if provider not in {"ollama", "aws_bedrock"} and not api_key_set:
            continue
        eligible.append(model)
    return eligible


def _build_no_inference_provider_response() -> JSONResponse:
    """Client must connect their own LLM; developer guard credentials are not used for inference."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "no_provider_configured",
            "message": (
                "No organization inference model is configured. "
                "Connect your provider API key under Firewall → Multi-Model Governance (1.5) → Model Connection. "
                "Organization keys are encrypted at rest and used for inference billing. "
                "ZeroShield guard models are used only for input and output scanning."
            ),
            "code": "no_provider_configured",
            "blocked_by": "model_routing",
            "category": "inference_not_configured",
        },
    )


def _validate_org_inference_model(
    *,
    requested_model: str,
    body: dict,
    inference_models: list[dict],
) -> JSONResponse | None:
    """Return an error response when inference would use a non-org model."""
    allowed = _routing_identity_set(inference_models)
    if not allowed:
        return _build_no_inference_provider_response()

    final_model = str(body.get("model") or requested_model or "").strip()
    if final_model.lower() in _guard_model_names():
        return JSONResponse(
            status_code=422,
            content={
                "error": "guard_model_not_for_inference",
                "message": (
                    "ZeroShield Guard Model is for input/output scanning only. "
                    "Connect your organization's inference model under Model Connection (module 1.5)."
                ),
                "code": "guard_model_not_for_inference",
                "blocked_by": "model_routing",
                "category": "inference_not_configured",
            },
        )
    if final_model and final_model not in allowed and final_model != "auto":
        return JSONResponse(
            status_code=422,
            content={
                "error": "model_not_configured",
                "message": (
                    f"Model '{final_model}' is not configured for organization inference. "
                    "Add it under Model Connection with your provider API key."
                ),
                "code": "model_not_configured",
                "blocked_by": "model_routing",
                "category": "inference_not_configured",
            },
        )
    if final_model == "auto" or not final_model:
        return None
    if final_model not in allowed:
        return JSONResponse(
            status_code=422,
            content={
                "error": "model_not_configured",
                "message": f"Model '{final_model}' is not available for this organization.",
                "code": "model_not_configured",
                "blocked_by": "model_routing",
            },
        )
    return None


def _routing_identity_set(routing_models: list[dict] | None) -> set[str]:
    identities: set[str] = set()
    for model in routing_models or []:
        model_name = str(model.get("model_name") or "").strip()
        model_id = str(model.get("model_id") or "").strip()
        if model_name:
            identities.add(model_name)
        if model_id:
            identities.add(model_id)
    return identities


_OUTPUT_ACTION_PRIORITY = {
    "allow": 0,
    "flag": 1,
    "rewrite": 2,
    "redact": 3,
    "block": 4,
}


def _normalize_output_action(action: str | None) -> str:
    if action == "monitor":
        return "flag"
    if action in _OUTPUT_ACTION_PRIORITY:
        return str(action)
    return "allow"


def _set_completion_response_text(completion: dict, text: str) -> None:
    choices = completion.get("choices") or []
    if not choices:
        return
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return
    if isinstance(first_choice.get("message"), dict):
        first_choice["message"]["content"] = text
        first_choice["message"]["role"] = first_choice["message"].get("role") or "assistant"
        return
    if isinstance(first_choice.get("delta"), dict):
        first_choice["delta"]["content"] = text
        return
    first_choice["message"] = {"role": "assistant", "content": text}


def _rewrite_output_response_text(threat_type: str, detail: str | None = None) -> str:
    safe_templates = {
        "hallucination": "I cannot verify that claim from the available evidence. Please confirm it with authoritative sources or request grounded citations.",
        "pii": "Sensitive personal information was removed from the generated response.",
        "secret": "Sensitive credentials or secrets were removed from the generated response.",
        "credential": "Sensitive credentials or secrets were removed from the generated response.",
        "ip_leakage": "Sensitive infrastructure details were removed from the generated response.",
        "policy_violation": "The original model output was rewritten to comply with response safety policy.",
    }
    base_message = safe_templates.get(threat_type or "", safe_templates["policy_violation"])
    if detail and threat_type == "hallucination":
        return f"{base_message} Review detail: {detail}"
    return base_message


def _merge_output_enforcement_state(existing: dict | None, candidate: dict | None) -> dict | None:
    if candidate is None:
        return existing

    normalized_candidate = dict(candidate)
    normalized_candidate["action"] = _normalize_output_action(normalized_candidate.get("action"))
    if existing is None:
        return normalized_candidate

    current_priority = _OUTPUT_ACTION_PRIORITY.get(_normalize_output_action(existing.get("action")), 0)
    candidate_priority = _OUTPUT_ACTION_PRIORITY.get(normalized_candidate["action"], 0)
    merged = dict(existing if current_priority > candidate_priority else normalized_candidate)
    merged["review_required"] = bool(existing.get("review_required")) or bool(normalized_candidate.get("review_required"))
    merged["security_incident"] = bool(existing.get("security_incident")) or bool(normalized_candidate.get("security_incident"))
    merged["factuality_warning"] = bool(existing.get("factuality_warning")) or bool(normalized_candidate.get("factuality_warning"))
    if merged.get("action") == "allow" and merged.get("review_required"):
        merged["action"] = "flag"
    return merged


def _is_tier2_degraded_verdict(verdict) -> bool:
    if verdict is None:
        return False
    if verdict.tier != "tier_2":
        return False
    if verdict.threat_type == "bedrock_degraded":
        return True
    return str(getattr(verdict, "reason_code", "")).startswith("degraded")


def _resolve_success_metadata_from_verdict(scan_verdict) -> tuple[str, str, str, float, list[str], str]:
    """
    Resolve action/reason/threat/confidence from scanner verdict for successful
    (HTTP 200) responses so flagged Tier-2 results are not flattened.
    """
    if scan_verdict is None:
        return (
            "allow",
            "All security checks passed. No threats detected.",
            "none",
            0.0,
            [],
            "",
        )

    action = scan_verdict.action if scan_verdict.action in ("allow", "flag") else "allow"
    threat_type = scan_verdict.threat_type or "none"
    confidence = float(scan_verdict.confidence or 0.0)
    matched_patterns = list(scan_verdict.matched_patterns or [])
    detail = scan_verdict.detail or ""
    if action == "flag":
        reason = detail or f"Scanner flagged potential threat ({threat_type})."
    else:
        reason = detail or "All security checks passed. No threats detected."

    return action, reason, threat_type, confidence, matched_patterns, detail


def _build_block_response(
    status_code: int,
    code: str,
    zeroshield: dict,
    *,
    stage_metrics: dict | None = None,
    prompt: str = "",
    route_metadata: dict | None = None,
    requested_model: str = "",
    scan_verdict=None,
    output_scan_verdict=None,
) -> JSONResponse:
    """
    Build a unified blocked JSONResponse with zeroshield metadata.
    CRITICAL: Uses _build_safe_block_response to NEVER expose sensitive details.
    In zeroshield dict: extracts threat category, redacts internal details, and keeps only safe metadata.
    """
    from pipeline_trace import build_pipeline_trace

    threat_category = zeroshield.get("threat_type", "policy_violation")
    request_id = zeroshield.get("request_id")
    internal_detail = zeroshield.get("detail")  # Will be logged server-side only
    detection_tier = str(zeroshield.get("detection_tier") or "")
    blocked_stage = _resolve_pipeline_blocked_by(
        code=code,
        threat_category=threat_category,
        detection_tier=detection_tier,
    )
    pipeline_trace = build_pipeline_trace(
        prompt=prompt,
        stage_metrics=stage_metrics,
        final_action="block",
        blocked_stage=blocked_stage,
        http_status=status_code,
        scan_verdict=scan_verdict,
        zeroshield=zeroshield,
        route_metadata=route_metadata,
        blocked_detail=internal_detail or "",
        requested_model=requested_model,
        output_scan_verdict=output_scan_verdict,
    )

    return _build_safe_block_response(
        status_code=status_code,
        code=code,
        threat_category=threat_category,
        request_id=request_id,
        internal_detail=internal_detail,
        detection_tier=detection_tier,
        pipeline_trace=pipeline_trace,
    )


def _audit_fire_and_forget(
    *,
    org_slug: str,
    decision: str,
    rule_code: str,
    metadata: dict | None = None,
) -> None:
    """D_G5: emit a query-audit event without blocking the hot path.

    Safe to call from anywhere; never raises. ``decision == "allow"`` is a
    no-op inside ``emit_query_audit_event`` so callers don't have to branch.
    """
    if not org_slug:
        return
    try:
        from telemetry_ops import emit_query_audit_event, _log_task_exception  # type: ignore
        task = asyncio.create_task(
            emit_query_audit_event(
                org_slug=org_slug,
                decision=decision,
                rule_code=str(rule_code or "unknown"),
                metadata=metadata or {},
            )
        )
        task.add_done_callback(_log_task_exception)
    except Exception as _exc:  # never break the request flow
        LOG.debug("audit fire-and-forget skipped: %r", _exc)


_CATEGORY_THREAT_MAP: dict[str, str] = {
    "threat_detection": "prompt_injection",
    "pii_protection": "pii",
    "data_protection": "data_leakage",
    "content_safety": "toxicity",
    "compliance": "compliance_violation",
    "rag_security": "rag_poisoning",
    "access_control": "access_violation",
}


def _category_to_threat_type(category: str) -> str:
    """Map a policy category to a threat_type for telemetry enrichment."""
    return _CATEGORY_THREAT_MAP.get(category, "policy_violation")


# ── Per-request context for telemetry enrichment ──
# Set at the start of proxy_chat so all build_telemetry_event calls
# within that request automatically pick up org/IP/method/status.
import contextvars as _ctxvars

_REQUEST_ORG_ID: _ctxvars.ContextVar[int | None] = _ctxvars.ContextVar("_req_org_id", default=None)
_REQUEST_ORG_SLUG: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("_req_org_slug", default="")
_REQUEST_SOURCE_IP: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("_req_src_ip", default="")
_REQUEST_METHOD: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("_req_method", default="POST")
_CHAT_REQUEST_ID: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("_chat_request_id", default="")

# Event types that represent isolation / kill-switch / circuit-breaker actions.
# These are security-critical and MUST always be recorded, even when an org
# has disabled audit logging — otherwise a tenant could silence its own
# containment trail.
_ALWAYS_AUDIT_EVENT_TYPES = ("kill_switch", "model_isolation", "circuit_breaker")


def _org_audit_logging_enabled() -> bool:
    """Resolve the current request org's ``audit_logging_enabled`` setting.

    The control plane maps ``FirewallConfig.audit_logging_enabled`` onto the
    gateway config key ``telemetry_enabled`` (per-org, synced to Redis). This
    helper reads it for the *current request's* org so telemetry/audit
    emission can be skipped when a tenant has turned audit logging off.

    Defaults to ``True`` whenever no per-org value is resolvable, preserving
    backward-compatible behavior for unauthenticated / no-org requests.
    """
    try:
        slug = _REQUEST_ORG_SLUG.get()
        if slug and CONFIG_SYNC is not None:
            oc = CONFIG_SYNC.get_config(slug)
            if oc is not None and "telemetry_enabled" in oc:
                return bool(oc.get("telemetry_enabled", True))
    except Exception:  # noqa: BLE001 — never let the audit gate break the hot path
        pass
    return bool(CONFIG.get("telemetry_enabled", True))


def _telemetry_owasp_metadata(threat_type: str = "", *, verdict=None, extra: dict | None = None) -> dict:
    """Resolve OWASP vector codes for gateway telemetry metadata."""
    from ai_mesh_shared.owasp_telemetry import resolve_owasp_codes

    payload = dict(extra or {})
    codes_from_verdict = list(getattr(verdict, "owasp_codes", None) or [])
    if codes_from_verdict:
        payload.setdefault("owasp_codes", codes_from_verdict)
    codes = resolve_owasp_codes(threat_type or "", payload)
    return {"owasp_codes": codes} if codes else {}


def _emit_telemetry(status_code: int = 200, **kwargs):
    """Convenience: build_telemetry_event + inject per-request context + emit."""
    if TELEMETRY is None:
        return
    from telemetry import build_telemetry_event
    kwargs.setdefault("organization_id", _REQUEST_ORG_ID.get())
    kwargs.setdefault("source_ip", _REQUEST_SOURCE_IP.get())
    kwargs.setdefault("method", _REQUEST_METHOD.get())
    req_id = _CHAT_REQUEST_ID.get("")
    if req_id:
        md = dict(kwargs.get("metadata") or {})
        md.setdefault("request_id", req_id)
        kwargs["metadata"] = md
    kwargs.setdefault("status_code", status_code)
    event_type = kwargs.get("event_type") or ""
    _is_isolation = event_type in _ALWAYS_AUDIT_EVENT_TYPES
    # Per-org audit-logging gate (audit_logging_enabled → telemetry_enabled).
    # When a tenant disables audit logging we skip emission, EXCEPT for
    # isolation/kill-switch/circuit-breaker events which are always recorded.
    if not _is_isolation and not _org_audit_logging_enabled():
        return
    if _is_isolation:
        md = dict(kwargs.get("metadata") or {})
        md["is_isolation_event"] = True
        kwargs["metadata"] = md
        if event_type == "kill_switch" and float(kwargs.get("risk_score") or 0) <= 0.60:
            kwargs["risk_score"] = 0.85
    event = build_telemetry_event(**kwargs)
    TELEMETRY.emit(event)
    # Phase 1 pilot: best-effort fan-out to Mongo append-only sink.
    # Gated by GATEWAY_MONGO_TELEMETRY_ENABLED; never blocks the hot path.
    try:
        from telemetry_mongo import is_mongo_telemetry_enabled, record_enforcement_event
        if is_mongo_telemetry_enabled():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(record_enforcement_event(event if isinstance(event, dict) else dict(event)))
            except RuntimeError:
                pass  # No running loop (e.g., called from sync context); skip silently.
    except Exception:
        pass


async def _enforce_org_tpm_rate_limit(
    auth_ctx,
    *,
    event_type: str,
    model: str = "",
    user_id: int | None = None,
    project_id: str = "",
    estimated_tokens: int = 20,
) -> JSONResponse | None:
    """
    Phase 1 hardening (Module 1 §1.1): enforce the per-org TPM ceiling on
    any endpoint that consumes upstream model / vector-DB capacity.

    Thin shim — actual logic in rate_limit_enforcement.py so it can be unit-
    tested without dragging the full main.py import chain (Py3.9 CI).
    """
    from rate_limit_enforcement import enforce_org_tpm_rate_limit

    return await enforce_org_tpm_rate_limit(
        auth_ctx,
        rate_limiter=RATE_LIMITER,
        config_sync=CONFIG_SYNC,
        metrics=METRICS,
        emit_telemetry=_emit_telemetry,
        event_type=event_type,
        model=model,
        user_id=user_id,
        project_id=project_id,
        estimated_tokens=estimated_tokens,
    )


def _require_admin_role(request) -> JSONResponse | None:
    """
    Phase A.2b hardening (Module 1): gate /v1/admin/* endpoints behind an
    admin RBAC check. Returns a JSONResponse (401/403) when the caller is
    not admin — handler must return it verbatim.

    Phase 1 Fx-2a: accept a server-to-server bypass when the request carries
    ``X-Internal-Key`` matching ``GATEWAY_INTERNAL_API_KEY``. This lets the
    control plane proxy admin reads/mutations on behalf of a Django-RBAC'd
    user without exposing gateway admin endpoints to user JWTs directly.

    Thin shim — actual predicate in admin_auth.py so it is Py3.9-testable
    without dragging the full main.py import chain.
    """
    from admin_auth import require_admin

    # Header name matches the existing mcp_proxy convention so the control
    # plane can use a single shared secret for all server-to-server calls.
    internal_key = os.environ.get("GATEWAY_INTERNAL_API_KEY", "").strip()
    if internal_key:
        header_key = (request.headers.get("x-gateway-internal-key") or "").strip()
        if header_key and header_key == internal_key:
            return None

    auth_ctx = getattr(request.state, "auth_context", None)
    return require_admin(auth_ctx)


def _maybe_emit_critical_alert(
    threat_type: str,
    confidence: float,
    user_id: int | None = None,
    detail: str = "",
    org_slug: str = "",
) -> None:
    """
    If alerting is enabled and the confidence score exceeds the critical
    threshold, emit a critical_alert telemetry event for backend processing.
    """
    alert_cfg = CONFIG
    if org_slug and CONFIG_SYNC is not None:
        alert_cfg = CONFIG_SYNC.get_config(org_slug)
    if not alert_cfg.get("alerting_enabled", True):
        return
    if TELEMETRY is None:
        return
    risk_score_pct = int(confidence * 100)
    critical_threshold = alert_cfg.get("critical_alert_threshold", 90)
    if risk_score_pct >= critical_threshold:
        _emit_telemetry(
            event_type="critical_alert",
            action="alert",
            threat_type=threat_type,
            risk_score=confidence,
            user_id=user_id,
            compliance_tags=alert_cfg.get("compliance_frameworks", []),
            metadata={
                "alert_level": "critical",
                "risk_score_pct": risk_score_pct,
                "critical_threshold": critical_threshold,
                "detail": detail,
                "alert_recipients": alert_cfg.get("alert_recipients", ""),
                "module": "1.2",
                "module_id": "1.2",
            },
        )
        LOG.warning(
            "Critical alert emitted: threat_type=%s, risk_score=%d%%, threshold=%d%%, user=%s",
            threat_type, risk_score_pct, critical_threshold, user_id,
        )


async def _telemetry_loop():
    while True:
        await asyncio.sleep(CONFIG["stats_interval_sec"])
        if not AGENT_ID:
            continue
        url = f"{CONFIG['backend_url']}/api/gateways/instances/telemetry/"
        total = METRICS["total_requests"]
        blocked = METRICS["blocked"]
        allowed = METRICS["allowed"]
        sum_ms = METRICS["sum_latency_ms"]
        avg_ms = sum_ms / (total - blocked) if (total - blocked) > 0 else 0
        payload = {
            "agent_id": AGENT_ID,
            "total_requests": total,
            "blocked": blocked,
            "allowed": allowed,
            "avg_latency_ms": round(avg_ms, 2),
            "active_connections": METRICS["active_connections"],
            "location": CONFIG.get("gateway_location") or "",
        }
        code, _ = await asyncio.to_thread(
            _http_request, "POST", url, data=payload, api_key=CONFIG.get("api_key")
        )
        if code == 200:
            LOG.debug("Telemetry sent")


@app.on_event("startup")
async def startup():
    global CONFIG, CONFIG_SYNC, LLM_ROUTER, POLICY_SYNC, RATE_LIMITER, INPUT_SCANNER
    global VECTOR_POLICY_SYNC, VECTOR_CLIENTS, CONTEXT_GUARD, VECTOR_PROVIDER_SYNC
    global REDIS_CLIENT, TELEMETRY, OUTPUT_GUARD, CIRCUIT_BREAKER
    global BEDROCK_EMBEDDER, GROUNDING_GUARD
    import sys

    CONFIG = load_config()
    app.state.config = CONFIG

    # Phase 0 D-G1-v3: detect POLICY_SIGNING_KEY misconfiguration at startup.
    # We intentionally do NOT SystemExit here; that is deferred to release
    # N+2 per docs/UPGRADE.md. Instead: log CRITICAL, emit a Mongo event,
    # and let /health return 503 until operator fixes config.
    if signing_enforced() and _get_signing_key() is None:
        LOG.critical(
            "POLICY_SIGNING_KEY is not set but GATEWAY_POLICY_SIGNING_REQUIRED is true. "
            "Gateway will refuse all policy bundles and /health will report 503. "
            "Set POLICY_SIGNING_KEY explicitly or set GATEWAY_POLICY_SIGNING_REQUIRED=false "
            "for the cutover window only. SystemExit on this condition is deferred to "
            "release N+2; see docs/UPGRADE.md."
        )
        asyncio.create_task(
            emit_operational_event(
                EVENT_CLASS_POLICY_HMAC_MISCONFIG,
                severity="critical",
                metadata={
                    "reason": "POLICY_SIGNING_KEY missing while signing enforced",
                    "site": "startup",
                },
            )
        )
    from config_sync import ConfigSync
    CONFIG_SYNC = ConfigSync(redis_url=CONFIG["redis_url"], config=CONFIG)
    await CONFIG_SYNC.start()
    if CONFIG["backend_url"] and not CONFIG.get("allow_http_backend", True):
        LOG.error(
            "Backend URL must use HTTPS in production. Set AIGUARDX_BACKEND_URL to https://... "
            "or set GATEWAY_ALLOW_HTTP=1 only for development."
        )
        sys.exit(1)
    if CONFIG.get("upstream_llm_url") and not CONFIG.get("allow_http_llm", True):
        LOG.error(
            "Upstream LLM URL must use HTTPS in production. Set GATEWAY_UPSTREAM_LLM_URL to https://... "
            "or set GATEWAY_ALLOW_HTTP_LLM=1 only for development."
        )
        sys.exit(1)

    from llm_router import LLMRouter

    LLM_ROUTER = LLMRouter(CONFIG)
    # Ensure Redis-backed model routes are active immediately at startup.
    if CONFIG_SYNC is not None:
        await CONFIG_SYNC.reload_models_now()

    from rate_limiter import RateLimiter

    RATE_LIMITER = RateLimiter(redis_url=CONFIG["redis_url"])

    # ── Embedding Vault (Tier-1.6 semantic injection check) ──
    # Must be initialized BEFORE InputScanner so it can be injected.
    # Vault auto-disables when DATABASE_URL/vault_db_dsn is unset (zero overhead).
    _embedding_vault = None
    if CONFIG.get("embedding_vault_enabled", True):
        try:
            from embedding_vault import EmbeddingVault
            _embedding_vault = EmbeddingVault(
                pg_dsn=CONFIG.get("vault_db_dsn", ""),
                similarity_threshold=CONFIG.get("embedding_vault_threshold", 0.35),
            )
            LOG.info(
                "Embedding Vault initialized (enabled=%s, threshold=%.2f)",
                _embedding_vault.enabled,
                CONFIG.get("embedding_vault_threshold", 0.35),
            )
        except Exception as exc:  # noqa: BLE001 - vault init failures must not block startup
            LOG.warning("EmbeddingVault initialization failed: %s", exc)
            _embedding_vault = None

    if CONFIG.get("input_scan_enabled", True):
        from scanner import InputScanner

        INPUT_SCANNER = InputScanner(
            thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
            config=CONFIG,
            embedding_vault=_embedding_vault,
            config_sync=CONFIG_SYNC,
        )

    # Shared async Redis client for kill-switch + telemetry
    import redis.asyncio as aioredis

    try:
        REDIS_CLIENT = aioredis.from_url(
            CONFIG["redis_url"],
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=2,
        )
        LOG.info("Shared async Redis client initialized")
    except Exception:
        LOG.exception("Failed to create shared async Redis client")

    # Telemetry producer
    if CONFIG.get("telemetry_enabled", True) and REDIS_CLIENT is not None:
        from telemetry import TelemetryProducer

        TELEMETRY = TelemetryProducer(
            redis_client=REDIS_CLIENT,
            flush_interval=CONFIG.get("telemetry_flush_interval", 2.0),
            max_buffer_size=CONFIG.get("telemetry_buffer_size", 100),
        )
        await TELEMETRY.start()

    # Output guard
    if INPUT_SCANNER is not None and CONFIG.get("output_guard_enabled", True):
        from output_guard import OutputGuard

        OUTPUT_GUARD = OutputGuard(scanner=INPUT_SCANNER, config=CONFIG)
        LOG.info("Output guard initialized")

    # Semantic leakage detector (attach to output guard)
    if OUTPUT_GUARD is not None and CONFIG.get("semantic_leakage_enabled", False) and REDIS_CLIENT is not None:
        from leakage_detector import SemanticLeakageDetector

        _leakage = SemanticLeakageDetector(redis_client=REDIS_CLIENT)
        OUTPUT_GUARD.set_leakage_detector(_leakage)
        LOG.info("Semantic leakage detector attached to output guard")

    # Circuit breaker (auto-trigger on error rate threshold)
    if CONFIG.get("circuit_breaker_enabled", True) and REDIS_CLIENT is not None:
        from circuit_breaker import CircuitBreaker

        CIRCUIT_BREAKER = CircuitBreaker(
            redis_client=REDIS_CLIENT,
            error_threshold=CONFIG.get("circuit_breaker_error_threshold", 0.5),
            min_requests=CONFIG.get("circuit_breaker_min_requests", 10),
            cooldown_seconds=CONFIG.get("circuit_breaker_cooldown_seconds", 120),
        )
        LOG.info("Circuit breaker initialized")

    # Semantic grounding (Bedrock Titan v2) -> attach to output guard
    if (
        OUTPUT_GUARD is not None
        and CIRCUIT_BREAKER is not None
        and CONFIG.get("output_grounding_enabled", True)
    ):
        try:
            from rag_pipeline.bedrock_embedder import BedrockEmbedder
            from rag_pipeline.grounding_guard import GroundingGuard
            from bedrock_client import default_bedrock_client

            BEDROCK_EMBEDDER = BedrockEmbedder(
                bedrock_client=default_bedrock_client(),
                circuit_breaker=CIRCUIT_BREAKER,
            )
            GROUNDING_GUARD = GroundingGuard(embedder=BEDROCK_EMBEDDER)
            OUTPUT_GUARD.set_grounding_guard(GROUNDING_GUARD)
            LOG.info(
                "Semantic grounding (Bedrock Titan v2) attached to output guard"
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning(
                "Failed to initialize semantic grounding guard: %s (lexical fallback in effect)",
                exc,
            )

    if CONFIG.get("policy_cache_enabled", True):
        from policy_sync import PolicySync

        POLICY_SYNC = PolicySync(redis_url=CONFIG["redis_url"])
        await POLICY_SYNC.start()

    if CONFIG.get("rag_enabled", False):
        from vector_policy_sync import VectorPolicySync
        from vector_client import PineconeClient, MilvusClient, ChromaDBClient
        from context_guard import ContextGuard

        VECTOR_POLICY_SYNC = VectorPolicySync(redis_url=CONFIG["redis_url"])
        await VECTOR_POLICY_SYNC.start()

        # Phase 1 F-3.1: local ChromaDB is the default zero-config backend
        # for the RAG Collection Manager. Registered before the cloud
        # providers so it surfaces first in the UI dropdown.
        if CONFIG.get("chroma_url"):
            try:
                VECTOR_CLIENTS["chroma"] = ChromaDBClient(
                    url=CONFIG["chroma_url"],
                    auth_token=CONFIG.get("chroma_auth_token", ""),
                    thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
                )
                LOG.info("ChromaDB vector client registered (url=%s)", CONFIG["chroma_url"])
            except Exception as exc:  # noqa: BLE001
                LOG.warning("ChromaDB client registration failed: %s", exc)

        if CONFIG.get("pinecone_api_key"):
            VECTOR_CLIENTS["pinecone"] = PineconeClient(
                api_key=CONFIG["pinecone_api_key"],
                environment=CONFIG.get("pinecone_environment", ""),
                embedding_model=CONFIG.get("pinecone_embedding_model", "text-embedding-3-small"),
                thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
            )
            LOG.info("Pinecone vector client registered")

        if CONFIG.get("milvus_uri"):
            VECTOR_CLIENTS["milvus"] = MilvusClient(
                uri=CONFIG["milvus_uri"],
                token=CONFIG.get("milvus_token", ""),
                thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
            )
            LOG.info("Milvus vector client registered (uri=%s)", CONFIG["milvus_uri"])

        CONTEXT_GUARD = ContextGuard(
            thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
        )

        # ── Org-level vector provider credential sync ──
        from vector_provider_sync import VectorProviderSync
        VECTOR_PROVIDER_SYNC = VectorProviderSync(redis_url=CONFIG["redis_url"])
        await VECTOR_PROVIDER_SYNC.start()

        LOG.info(
            "RAG Firewall initialized (vector_clients=%s, context_guard=enabled, provider_sync=%d)",
            list(VECTOR_CLIENTS.keys()),
            VECTOR_PROVIDER_SYNC.provider_count,
        )

        # ── Pipeline-Aware RAG Firewall (4-stage governed pipeline) ──
        global RAG_PIPELINE
        from rag_pipeline import RAGFirewallPipeline

        _leakage_for_rag = None
        if CONFIG.get("semantic_leakage_enabled", True) and REDIS_CLIENT is not None:
            from leakage_detector import SemanticLeakageDetector
            _leakage_for_rag = SemanticLeakageDetector(redis_client=REDIS_CLIENT)
            LOG.info("Semantic leakage detector enabled")

        # ── New detection layers ──
        _llm_judge = None
        if CONFIG.get("llm_judge_enabled", True):
            from llm_judge import LLMJudge
            _bedrock_model = CONFIG.get("llm_judge_model") or os.getenv("BEDROCK_MODEL", "openai.gpt-oss-120b-1:0")
            _llm_judge = LLMJudge(model=_bedrock_model)
            LOG.info("LLM Judge initialized (bedrock_model=%s)", _bedrock_model)

        # Embedding Vault already initialized above (hoisted before InputScanner
        # so it can be injected as the Tier-1.6 semantic-injection check).
        # The same instance is reused here by the RAG pipeline.

        _canary_manager = None
        if CONFIG.get("canary_tokens_enabled", True):
            from canary_tokens import CanaryTokenManager
            _canary_manager = CanaryTokenManager()
            LOG.info("Canary Token Manager initialized")

        _intent_classifier = None
        if CONFIG.get("intent_classifier_enabled", True):
            from intent_classifier import IntentClassifier
            _intent_classifier = IntentClassifier()
            LOG.info("Intent Classifier initialized")

        RAG_PIPELINE = RAGFirewallPipeline(
            input_scanner=INPUT_SCANNER,
            context_guard=CONTEXT_GUARD,
            leakage_detector=_leakage_for_rag,
            vector_clients=VECTOR_CLIENTS,
            circuit_breaker=CIRCUIT_BREAKER,
            rate_limiter=RATE_LIMITER,
            telemetry=TELEMETRY,
            redis_client=REDIS_CLIENT,
            config=CONFIG,
            policy_sync=POLICY_SYNC,
            llm_judge=_llm_judge,
            embedding_vault=_embedding_vault,
            canary_token_manager=_canary_manager,
            intent_classifier=_intent_classifier,
        )
        LOG.info("RAG Firewall Pipeline initialized (stages=4, escalation_levels=3, detection_layers=%d)",
                 sum(1 for x in [_llm_judge, _embedding_vault, _canary_manager, _intent_classifier, _leakage_for_rag] if x))

    # ── Centralized logging via Redis Pub/Sub ──
    from ai_mesh_shared.redis_log_handler import RedisLogPublisher

    _redis_log_publisher = RedisLogPublisher(
        redis_url=CONFIG["redis_url"],
        service_name="Gateway",
    )
    _redis_log_publisher.setFormatter(logging.Formatter("%(message)s"))

    # Attach to parent "gateway" logger only. Children (gateway.scanner,
    # gateway.middleware, etc.) propagate=True by default, so their records
    # reach this handler without separate attachment. This fixes the
    # duplicate-record bug in the old BufferHandler setup.
    gateway_logger = logging.getLogger("gateway")
    gateway_logger.addHandler(_redis_log_publisher)
    gateway_logger.setLevel(logging.DEBUG)

    # Bedrock logger has propagate=False, so it needs its own publisher.
    bedrock_logger = logging.getLogger("bedrock")
    bedrock_logger.addHandler(RedisLogPublisher(
        redis_url=CONFIG["redis_url"],
        service_name="Bedrock",
    ))

    # Subscribe to Redis Pub/Sub and feed into local LogBuffer for SSE
    from log_buffer import LOG_BUFFER, RedisLogSubscriber

    global _log_subscriber
    _log_subscriber = RedisLogSubscriber(
        redis_url=CONFIG["redis_url"],
        buffer=LOG_BUFFER,
    )
    await _log_subscriber.start()
    LOG.info("Centralized log pipeline started (Redis Pub/Sub -> LogBuffer -> SSE)")

    # Seed the buffer so the viewer always has something on first connect
    LOG.info("ZeroShield Gateway ready \u2014 real-time log streaming active")

    # Initialize dedicated Bedrock logger (separate file + ring buffer)
    try:
        from bedrock_logger import bedrock_log as _bedrock_log, BEDROCK_LOG_RING
        LOG.info(
            "Bedrock dedicated logger initialized: log_dir=%s, level=%s, json=%s, ring_size=%d",
            os.getenv("BEDROCK_LOG_DIR", "/var/log/bedrock"),
            os.getenv("BEDROCK_LOG_LEVEL", "DEBUG"),
            os.getenv("BEDROCK_LOG_JSON", "true"),
            BEDROCK_LOG_RING._buf.maxlen,
        )
    except Exception as exc:
        LOG.warning("Bedrock dedicated logger failed to initialize: %s", exc)

    # MCP Proxy (Secure-MCP-Gateway + direct upstream + stdio adapter)
    try:
        from mcp_proxy import router as mcp_proxy_router, org_gateway_router
    except ImportError:
        from .mcp_proxy import router as mcp_proxy_router, org_gateway_router
    app.include_router(mcp_proxy_router)
    app.include_router(org_gateway_router)
    LOG.info("MCP proxy routes mounted at /v1/mcp")
    LOG.info("MCP org gateway routes mounted at /gateway/{org}/mcp/{server}")

    # MCP OAuth 2.1 (enables VS Code "Add MCP Server" auto-auth flow)
    try:
        from mcp_oauth import router as mcp_oauth_router
    except ImportError:
        from .mcp_oauth import router as mcp_oauth_router
    app.include_router(mcp_oauth_router)
    LOG.info("MCP OAuth 2.1 routes mounted (/.well-known/oauth-authorization-server, /oauth/*)")

    # MCP Upstream OAuth Proxy (per-org OAuth for external MCP servers like Linear)
    try:
        from mcp_oauth_proxy import router as mcp_oauth_proxy_router
    except ImportError:
        from .mcp_oauth_proxy import router as mcp_oauth_proxy_router
    app.include_router(mcp_oauth_proxy_router)
    LOG.info("MCP upstream OAuth proxy mounted (/gateway/{org}/mcp/{server}/oauth/*)")

    # Vector Operations API (/v1/vector/*) — see vector_routes.py for the
    # full route surface (query, upsert, delete, config). The router was
    # historically defined but never mounted, leaving the documented
    # vector data-plane unreachable; this restores it. Mounting is purely
    # additive — endpoints enforce the same per-request firewall checks
    # and SSRF/payload guards as the legacy gateway routes.
    try:
        from vector_routes import router as vector_router
    except ImportError:
        from .vector_routes import router as vector_router
    app.include_router(vector_router)
    LOG.info("Vector operations routes mounted at /v1/vector/*")

    # MCP Stdio & WebSocket adapter lifecycle
    try:
        from mcp_stdio_adapter import start_reaper as stdio_start_reaper
        from mcp_ws_adapter import start_reaper as ws_start_reaper
        stdio_start_reaper()
        ws_start_reaper()
        LOG.info("MCP stdio & WebSocket adapters initialized (reapers started)")
    except Exception as exc:
        LOG.warning("MCP adapter init failed (non-fatal): %s", exc)

    # Agent proxy excluded in AI Mesh Firewall standalone (device fleet not in SKU)

    if not CONFIG["backend_url"]:
        LOG.warning(
            "AIGUARDX_BACKEND_URL not set; gateway will not register or enforce"
        )
        return
    await asyncio.to_thread(_check_version)
    max_register_attempts = 5
    register_delay_sec = 2
    for attempt in range(1, max_register_attempts + 1):
        if await asyncio.to_thread(_register):
            break
        if attempt < max_register_attempts:
            LOG.warning("Registration failed (attempt %s/%s), retrying in %ss ...", attempt, max_register_attempts, register_delay_sec)
            await asyncio.sleep(register_delay_sec)
    else:
        LOG.warning("Gateway could not register after %s attempts; policy check will be skipped until backend is available.", max_register_attempts)

    if AGENT_ID:
        asyncio.create_task(_telemetry_loop())


@app.on_event("shutdown")
async def shutdown():
    if TELEMETRY is not None:
        await TELEMETRY.stop()
    if CONFIG_SYNC is not None:
        await CONFIG_SYNC.stop()
    if REDIS_CLIENT is not None:
        await REDIS_CLIENT.close()
    if POLICY_SYNC is not None:
        await POLICY_SYNC.stop()
    if VECTOR_POLICY_SYNC is not None:
        await VECTOR_POLICY_SYNC.stop()
    if _log_subscriber is not None:
        await _log_subscriber.stop()
    # Shut down MCP adapters
    try:
        from mcp_stdio_adapter import shutdown_all as stdio_shutdown
        await stdio_shutdown()
    except Exception:
        pass
    try:
        from mcp_ws_adapter import shutdown_all as ws_shutdown
        await ws_shutdown()
    except Exception:
        pass


def _detect_rag_request(body: dict, messages: list[dict]) -> bool:
    if body.get("rag_context") is not None:
        return True
    if body.get("documents") is not None:
        return True
    for msg in messages:
        if msg.get("role") == "system":
            content = (msg.get("content") or "").lower()
            if any(
                marker in content
                for marker in ("context:", "retrieved documents:", "knowledge base:", "search results:")
            ):
                return True
    return False


@app.post(
    "/v1/chat/completions",
    summary="Chat completions (OpenAI-compatible proxy)",
    description=(
        "Proxy endpoint for OpenAI-compatible chat completions.\n\n"
        "**Flow:**\n"
        "1. Authenticates the request via Gateway API Key\n"
        "2. Checks the model against the key's allowed_models list\n"
        "3. Runs optional security scan on the prompt\n"
        "4. Evaluates the prompt against enabled policies\n"
        "5. Forwards to the LLM provider via LiteLLM\n"
        "6. Optionally checks the response against policies\n"
        "7. Returns the LLM response (or blocks/redacts)\n\n"
        "**Streaming:** Set `stream: true` to receive Server-Sent Events (SSE). "
        "When streaming, post-response policy checks are skipped.\n\n"
        "**Headers:**\n"
        "- `Authorization: Bearer <gateway_api_key>` (required)\n"
        "- `X-User-ID: <int>` (optional, legacy)\n"
        "- `X-Endpoint-ID: <int>` (optional, legacy)\n"
        "- `X-Agent-Data: <base64_json>` (optional, agentic context)"
    ),
    response_description="OpenAI-compatible chat completion response or SSE stream",
    tags=["Chat Completions"],
    responses={
        200: {
            "description": "Successful completion (JSON or SSE stream)",
            "content": {
                "application/json": {
                    "example": {
                        "id": "chatcmpl-abc123",
                        "object": "chat.completion",
                        "created": 1700000000,
                        "model": "gpt-4o-mini",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Hello! How can I help you?",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 8,
                            "total_tokens": 18,
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid JSON body"},
        403: {
            "description": "Blocked by policy, security scan, input scanner, or model not in allowlist",
            "content": {
                "application/json": {
                    "examples": {
                        "policy_block": {
                            "value": {
                                "error": "blocked",
                                "message": "Request blocked by policy.",
                                "code": "content_blocked",
                            }
                        },
                        "model_not_allowed": {
                            "value": {
                                "error": "forbidden",
                                "message": "Model 'claude-3-opus' is not in your allowlist.",
                                "code": "model_not_allowed",
                            }
                        },
                        "input_scanner_block": {
                            "value": {
                                "error": "blocked",
                                "message": "Request blocked: prompt_injection detected.",
                                "code": "content_blocked",
                                "threat_type": "prompt_injection",
                            }
                        },
                    }
                }
            },
        },
        429: {
            "description": "Token rate limit exceeded (TPM)",
            "content": {
                "application/json": {
                    "example": {
                        "error": "rate_limited",
                        "message": "Token rate limit exceeded (95000/100000 TPM).",
                        "code": "rate_limit_exceeded",
                    }
                }
            },
        },
        502: {"description": "Upstream LLM request failed"},
        503: {
            "description": "Policy evaluation unavailable (fail-closed)",
            "content": {
                "application/json": {
                    "example": {
                        "error": "service_unavailable",
                        "message": "Policy evaluation unavailable. Request denied (fail-closed).",
                        "code": "policy_unavailable",
                    }
                }
            },
        },
    },
)
@app.post(
    "/v1/chat-completions",
    include_in_schema=False,
)
async def proxy_chat(
    request: Request,
    x_user_id: str | None = Header(None),
    x_endpoint_id: str | None = Header(None),
    x_agent_data: str | None = Header(None),
):
    """Proxy to upstream LLM after policy check. OpenAI-compatible endpoint."""
    METRICS["total_requests"] += 1
    METRICS["active_connections"] += 1
    start = time.perf_counter()
    stage_metrics = {
        "auth_ms": 0.0,
        "policy_ms": 0.0,
        "tier1_ms": 0.0,
        "tier2_ms": 0.0,
        "upstream_ms": 0.0,
        "telemetry_enqueue_ms": 0.0,
    }
    # When the control-plane policy engine already persisted an EnforcementEvent
    # (event_id in check response), skip the gateway's terminal request telemetry
    # so one user activity does not increment dashboards by 2.
    _policy_audit_event_id = None
    _gateway_lifecycle_telemetry_emitted = False

    # Set per-request telemetry context
    _REQUEST_SOURCE_IP.set(request.client.host if request.client else "")
    _REQUEST_METHOD.set(request.method)
    _CHAT_REQUEST_ID.set(
        (request.headers.get("X-Request-ID") or "").strip()
        or f"zs-{_uuid.uuid4().hex[:12]}"
    )

    try:
        try:
            raw_body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        try:
            body = normalize_openai_chat_request(raw_body, strip_unknown_top_level=True)
        except (ValueError, TypeError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": str(exc) or "Invalid chat completion request body.",
                    "code": "invalid_request_body",
                },
            )

        messages_raw = body.get("messages")
        if messages_raw is not None and not isinstance(messages_raw, list):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "'messages' must be an array.",
                    "code": "invalid_messages",
                },
            )
        if isinstance(messages_raw, list):
            for idx, msg in enumerate(messages_raw):
                if not isinstance(msg, dict):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "invalid_request",
                            "message": f"messages[{idx}] must be an object.",
                            "code": "invalid_messages",
                        },
                    )

        # Preserve routing controls that are not part of strict OpenAI payload schema.
        if isinstance(raw_body, dict):
            raw_routing_preferences = raw_body.get("routing_preferences")
            if isinstance(raw_routing_preferences, dict):
                body["routing_preferences"] = raw_routing_preferences

            raw_enable_routing = raw_body.get("enable_routing")
            if isinstance(raw_enable_routing, bool):
                body["enable_routing"] = raw_enable_routing

            raw_metadata = raw_body.get("metadata")
            if isinstance(raw_metadata, dict):
                metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
                if isinstance(raw_metadata.get("enable_routing"), bool):
                    metadata["enable_routing"] = raw_metadata["enable_routing"]
                if raw_metadata.get("data_sensitivity"):
                    metadata["data_sensitivity"] = raw_metadata["data_sensitivity"]
                raw_meta_compliance = raw_metadata.get("compliance_requirements")
                if raw_meta_compliance is not None:
                    metadata["compliance_requirements"] = raw_meta_compliance
                if metadata:
                    body["metadata"] = metadata

            # Isolation / routing controls stripped by OpenAI normalizer — restore for gateway only.
            if raw_body.get("data_sensitivity"):
                body["data_sensitivity"] = raw_body["data_sensitivity"]
            raw_compliance = raw_body.get("compliance_requirements")
            if raw_compliance is not None:
                body["compliance_requirements"] = raw_compliance

        # ── Identity: prefer auth_context from AuthMiddleware, fall back to legacy headers ──
        auth_ctx = getattr(request.state, "auth_context", None)
        stage_metrics["auth_ms"] = round((time.perf_counter() - start) * 1000, 2)

        route_selection = None
        route_metadata = None

        # ── Org-aware config lookup ──
        org_slug = auth_ctx.org_slug if auth_ctx else ""
        org_config = CONFIG_SYNC.get_config(org_slug) if CONFIG_SYNC else CONFIG
        routing_models = CONFIG_SYNC.get_model_routing(org_slug) if CONFIG_SYNC else []
        if not routing_models and CONFIG_SYNC is not None:
            await CONFIG_SYNC.reload_models_now(org_slug=org_slug)
            routing_models = CONFIG_SYNC.get_model_routing(org_slug)
        inference_models = _filter_inference_eligible_models(routing_models)
        scan_verdict = None
        routing_prefs = _extract_chat_routing_preferences(body, org_config, auth_ctx, scan_verdict)
        routing_active = bool(
            inference_models and LLM_ROUTER is not None and routing_prefs["routing_enabled"]
        )

        needs_inference = bool(body.get("stream")) or int(body.get("max_tokens") or 0) > 0
        if auth_ctx is not None and not inference_models and needs_inference:
            return _build_no_inference_provider_response()

        routing_identities = _routing_identity_set(inference_models)
        isolation_reroute_locked = False

        # Set org context for telemetry enrichment
        _REQUEST_ORG_ID.set(auth_ctx.organization_id if auth_ctx else None)
        _REQUEST_ORG_SLUG.set((getattr(auth_ctx, "org_slug", "") or "") if auth_ctx else "")

        # ── Master firewall toggle ──
        firewall_disabled = org_config.get("firewall_enabled") is False
        enforcement_mode = org_config.get("enforcement_mode", "block")
        tier2_execution_mode = str(org_config.get("tier2_execution_mode", "sync_pre_llm")).strip().lower()
        global_allowed_models = _coerce_string_list(org_config.get("allowed_models", []))
        if auth_ctx is not None:
            user_id = auth_ctx.user_id
            project_id = auth_ctx.project_id
            risk_score = auth_ctx.risk_score
            allowed_models = auth_ctx.allowed_models
            rate_limit_tpm = auth_ctx.rate_limit_tpm

            # Model allowlist enforcement (before any downstream calls)
            requested_model = body.get("model", "")
            if allowed_models and requested_model not in allowed_models and not routing_active:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=403,
                    event_type="input_blocked",
                    model=requested_model,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=0.50,
                    threat_type="model_not_allowed",
                    metadata={"detail": f"Model '{requested_model}' not in key allowlist"},
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Model '{requested_model}' is not in your allowlist.",
                        "code": "model_not_allowed",
                    },
                )
            if requested_model and routing_identities and requested_model not in routing_identities and not routing_active:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "model_not_configured",
                        "message": f"Model '{requested_model}' is not configured for external inference in this organization.",
                        "code": "model_not_configured",
                    },
                )
            endpoint_id = None  # Not used in GatewayAPIKey auth path
        else:
            # Legacy fallback (X-User-ID / X-Endpoint-ID headers)
            user_id = int(x_user_id) if x_user_id and x_user_id.isdigit() else None
            endpoint_id = (
                int(x_endpoint_id) if x_endpoint_id and x_endpoint_id.isdigit() else None
            )
            project_id = None
            risk_score = None
            allowed_models = None
            rate_limit_tpm = None
            requested_model = body.get("model", "")

        key_allowed_models = _coerce_string_list(allowed_models or [])
        routing_allowed_models = key_allowed_models or []
        if global_allowed_models:
            routing_allowed_models = [
                model for model in (routing_allowed_models or global_allowed_models) if model in global_allowed_models
            ]
        if not routing_allowed_models:
            routing_allowed_models = global_allowed_models or key_allowed_models

        if _is_routing_sentinel_model(requested_model) and inference_models:
            requested_model = _resolve_routing_hint_model(
                requested_model, org_config, inference_models
            )
            if requested_model and not _is_routing_sentinel_model(requested_model):
                body["model"] = requested_model

        # ── Global model isolation (from firewall config) ──
        if not firewall_disabled and org_config.get("model_isolation_enabled", False):
            global_allowed = global_allowed_models
            if (
                global_allowed
                and requested_model
                and not _is_routing_sentinel_model(requested_model)
                and requested_model not in global_allowed
                and not routing_active
            ):
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    _emit_telemetry(
                        status_code=403,
                        event_type="input_blocked",
                        model=requested_model,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.50,
                        threat_type="model_not_allowed",
                        metadata={"detail": f"Model '{requested_model}' not in global allowlist"},
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "forbidden",
                            "message": f"Model '{requested_model}' is not in the global allowlist.",
                            "code": "model_not_allowed",
                        },
                    )
                else:
                    LOG.warning(
                        "MONITOR: model '%s' not in global allowlist (user=%s)",
                        requested_model, user_id,
                    )

        # ── Threat intelligence check (risk_score from auth context) ──
        if not firewall_disabled and org_config.get("threat_intel_enabled", True):
            ctx_risk_score = getattr(auth_ctx, "risk_score", None) if auth_ctx else None
            if ctx_risk_score is not None:
                threat_threshold = org_config.get("threat_score_threshold", 75) / 100.0
                if ctx_risk_score >= threat_threshold and org_config.get("auto_block_threats", True):
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        _emit_telemetry(
                            status_code=403,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            threat_type="high_risk_actor",
                            risk_score=ctx_risk_score,
                            metadata={"detail": f"Risk score {ctx_risk_score:.2f} exceeds threshold {threat_threshold:.2f}"},
                        )
                        return _build_block_response(403, "threat_intel_blocked", _build_zeroshield_metadata(
                            action="block",
                            reason=f"Blocked by threat intelligence (risk_score={ctx_risk_score:.2f}, threshold={threat_threshold:.2f}).",
                            detection_tier="threat_intel",
                            threat_type="high_risk_actor",
                            confidence=ctx_risk_score,
                            original_prompt=body.get("messages", [{}])[-1].get("content", "") if body.get("messages") else "",
                            processing_time_ms=elapsed_ms,
                        ))
                    else:
                        LOG.warning(
                            "MONITOR: threat_intel would block user=%s risk_score=%.2f (threshold=%.2f)",
                            user_id, ctx_risk_score, threat_threshold,
                        )

        # ── Kill-switch check (Redis, ~0.1ms) ──
        if org_config.get("kill_switch_enabled", True):
            if REDIS_CLIENT is None:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=503,
                    event_type="kill_switch",
                    model=requested_model,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=0.60,
                    threat_type="kill_switch",
                    metadata={
                        "reason": "Kill-switch Redis unavailable — fail-closed",
                        "trigger_source": "kill_switch",
                        "kill_switch_truth_mode": "per_request_redis",
                    },
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": "Kill-switch enforcement unavailable.",
                        "code": "kill_switch_active",
                    },
                )
            from kill_switch import check_kill_switch
            try:
                from .metrics import record_kill_switch as _prom_ks
            except ImportError:
                from metrics import record_kill_switch as _prom_ks  # type: ignore[no-redef]

            ks_key_prefix = auth_ctx.prefix if auth_ctx else ""
            ks_verdict = await check_kill_switch(
                REDIS_CLIENT,
                requested_model,
                org_slug=org_slug,
                key_prefix=ks_key_prefix,
            )
            if ks_verdict.is_killed:
                _prom_ks(requested_model, ks_verdict.action or "block")
                ks_original = requested_model
                iso_ctx = _isolation_reroute_context(
                    org_slug, routing_models, routing_prefs, allowed_models, body=body
                )
                if ks_verdict.action == "reroute":
                    compliant_model, ks_audit = _apply_compliant_isolation_reroute(
                        primary_model=ks_original,
                        requested_fallback=ks_verdict.fallback_model,
                        ctx=iso_ctx,
                        scope=ks_verdict.scope or "org_model",
                        trigger_source="kill_switch",
                        reason=ks_verdict.reason,
                    )
                    if compliant_model:
                        LOG.warning(
                            "Kill-switch reroute: %s -> %s (reason: %s)",
                            ks_original, compliant_model, ks_verdict.reason,
                        )
                        body["model"] = compliant_model
                        requested_model = compliant_model
                        isolation_reroute_locked = True
                        ks_audit["kill_switch_action"] = "reroute"
                        _emit_telemetry(
                            event_type="kill_switch",
                            model=requested_model,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=ks_key_prefix,
                            action="reroute",
                            risk_score=0.60,
                            threat_type="kill_switch",
                            metadata=ks_audit,
                        )
                    else:
                        block_reason = (
                            f"{ks_verdict.reason}; no compliant fallback "
                            f"({ks_audit.get('fallback_reason_code', 'ineligible')})"
                        )
                        METRICS["blocked"] += 1
                        ks_audit["kill_switch_action"] = "block"
                        _emit_telemetry(
                            status_code=503,
                            event_type="kill_switch",
                            model=ks_original,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=ks_key_prefix,
                            action="block",
                            risk_score=0.60,
                            threat_type="kill_switch",
                            metadata={**ks_audit, "reason": block_reason},
                        )
                        return JSONResponse(
                            status_code=503,
                            content={
                                "error": "service_unavailable",
                                "message": f"Model '{ks_original}' is disabled (no compliant fallback).",
                                "code": "kill_switch_active",
                            },
                        )
                if ks_verdict.is_killed and ks_verdict.action != "reroute":
                    METRICS["blocked"] += 1
                    _emit_telemetry(
                        status_code=503,
                        event_type="kill_switch",
                        model=requested_model,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.60,
                        threat_type="kill_switch",
                        metadata={
                            "reason": ks_verdict.reason,
                            "original_model": ks_original,
                            "isolation_scope": ks_verdict.scope,
                            "trigger_source": "kill_switch",
                            "kill_switch_truth_mode": "per_request_redis",
                        },
                    )
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "service_unavailable",
                            "message": f"Model '{ks_original}' is currently disabled.",
                            "code": "kill_switch_active",
                        },
                    )

        # ── Model state check (Redis, org-scoped, ~0.1ms) ──
        if REDIS_CLIENT is not None:
            from model_state import check_model_state

            ms_verdict = await check_model_state(REDIS_CLIENT, requested_model, org_slug=org_slug or "default")
            ms_original = requested_model
            iso_ctx = _isolation_reroute_context(
                org_slug, routing_models, routing_prefs, allowed_models, body=body
            )

            if ms_verdict.status == "suspended":
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=503,
                    event_type="model_isolation",
                    model=ms_original,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=1.0,
                    threat_type="model_state_unavailable",
                    metadata={
                        "reason": ms_verdict.reason,
                        "original_model": ms_original,
                        "status": "suspended",
                        "trigger_source": "model_state",
                        "kill_switch_truth_mode": "per_request_redis",
                    },
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": f"Model state unavailable for '{ms_original}'.",
                        "code": "model_state_unavailable",
                        "reason": ms_verdict.reason,
                    },
                )

            if ms_verdict.status == "isolated":
                if ms_verdict.action == "reroute":
                    compliant_model, ms_audit = _apply_compliant_isolation_reroute(
                        primary_model=ms_original,
                        requested_fallback=ms_verdict.fallback_model,
                        ctx=iso_ctx,
                        scope="org_model",
                        trigger_source="model_state",
                        reason=ms_verdict.reason,
                    )
                    if compliant_model:
                        LOG.warning(
                            "Model-state reroute: %s -> %s (reason: %s)",
                            ms_original, compliant_model, ms_verdict.reason,
                        )
                        body["model"] = compliant_model
                        requested_model = compliant_model
                        isolation_reroute_locked = True
                        ms_audit["risk_score"] = ms_verdict.risk_score
                        ms_audit["threshold"] = ms_verdict.threshold
                        _emit_telemetry(
                            event_type="model_isolation",
                            model=requested_model,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="reroute",
                            risk_score=ms_verdict.risk_score / 100.0,
                            threat_type="model_isolated",
                            metadata=ms_audit,
                        )
                    else:
                        block_reason = (
                            f"{ms_verdict.reason}; no compliant fallback "
                            f"({ms_audit.get('fallback_reason_code', 'ineligible')})"
                        )
                        METRICS["blocked"] += 1
                        ms_audit["risk_score"] = ms_verdict.risk_score
                        ms_audit["threshold"] = ms_verdict.threshold
                        _emit_telemetry(
                            status_code=503,
                            event_type="model_isolation",
                            model=ms_original,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=ms_verdict.risk_score / 100.0,
                            threat_type="model_isolated",
                            metadata={**ms_audit, "reason": block_reason},
                        )
                        return JSONResponse(
                            status_code=503,
                            content={
                                "error": "service_unavailable",
                                "message": f"Model '{ms_original}' is isolated (no compliant fallback).",
                                "code": "model_isolated",
                                "reason": block_reason,
                            },
                        )
                if ms_verdict.status == "isolated" and ms_verdict.action == "alert":
                    LOG.warning(
                        "Model '%s' isolated with alert-only (risk=%.1f, reason=%s)",
                        requested_model, ms_verdict.risk_score, ms_verdict.reason,
                    )
                    _emit_telemetry(
                        event_type="model_isolation",
                        model=requested_model,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="alert",
                        risk_score=ms_verdict.risk_score / 100.0,
                        threat_type="model_isolated",
                        metadata={
                            "reason": ms_verdict.reason,
                            "risk_score": ms_verdict.risk_score,
                            "original_model": ms_original,
                            "trigger_source": "model_state",
                        },
                    )
                elif ms_verdict.status == "isolated":
                    METRICS["blocked"] += 1
                    _emit_telemetry(
                        status_code=503,
                        event_type="model_isolation",
                        model=ms_original,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=ms_verdict.risk_score / 100.0,
                        threat_type="model_isolated",
                        metadata={
                            "reason": ms_verdict.reason,
                            "risk_score": ms_verdict.risk_score,
                            "threshold": ms_verdict.threshold,
                            "original_model": ms_original,
                            "trigger_source": "model_state",
                        },
                    )
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "service_unavailable",
                            "message": f"Model '{ms_original}' is currently isolated (risk score: {ms_verdict.risk_score:.1f}).",
                            "code": "model_isolated",
                            "reason": ms_verdict.reason,
                            "risk_score": ms_verdict.risk_score,
                            "threshold": ms_verdict.threshold,
                        },
                    )

        # ── Per-org RPM + burst rate limiting (from firewall config) ──
        if (
            not firewall_disabled
            and org_config.get("rate_limit_enabled", CONFIG.get("rate_limit_enabled", True))
            and REDIS_CLIENT is not None
        ):
            # Each org enforces its own configured limit against its own Redis
            # counter. Unauthenticated requests (empty org_slug) share a single
            # "global" bucket so the key is never "ratelimit::...".
            rl_scope = org_slug or "global"
            now_ts = int(time.time())
            try:
                burst_limit = org_config.get("burst_limit", CONFIG.get("burst_limit", 150))
                second_bucket = now_ts
                burst_key = f"ratelimit:{rl_scope}:burst:{second_bucket}"
                current_burst = await REDIS_CLIENT.incr(burst_key)
                if current_burst == 1:
                    await REDIS_CLIENT.expire(burst_key, 2)
                if current_burst > burst_limit:
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        _emit_telemetry(
                            status_code=429,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.30,
                            threat_type="rate_limit_burst",
                            metadata={"detail": f"Burst limit exceeded ({current_burst}/{burst_limit} req/s)"},
                        )
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": "rate_limited",
                                "message": f"Burst limit exceeded ({current_burst}/{burst_limit} req/s).",
                                "code": "burst_limit_exceeded",
                            },
                            headers={"Retry-After": "1"},
                        )
                    else:
                        LOG.warning(
                            "MONITOR: burst limit exceeded (%d/%d req/s)",
                            current_burst, burst_limit,
                        )
            except Exception:
                LOG.warning("Burst rate check failed, skipping", exc_info=True)

            rpm_limit = org_config.get("requests_per_minute", CONFIG.get("requests_per_minute", 1000))
            minute_bucket = now_ts // 60
            rpm_key = f"ratelimit:{rl_scope}:{minute_bucket}"
            try:
                current_rpm = await REDIS_CLIENT.incr(rpm_key)
                if current_rpm == 1:
                    await REDIS_CLIENT.expire(rpm_key, 120)
                if current_rpm > rpm_limit:
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        _emit_telemetry(
                            status_code=429,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.30,
                            threat_type="rate_limit_rpm",
                            metadata={"detail": f"RPM limit exceeded ({current_rpm}/{rpm_limit})"},
                        )
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": "rate_limited",
                                "message": f"Rate limit exceeded ({current_rpm}/{rpm_limit} RPM).",
                                "code": "rate_limit_exceeded",
                            },
                            headers={"Retry-After": "60"},
                        )
                    else:
                        LOG.warning(
                            "MONITOR: RPM limit exceeded (%d/%d)",
                            current_rpm, rpm_limit,
                        )
            except Exception:
                LOG.warning("RPM check failed, skipping", exc_info=True)

        messages = body.get("messages") or []
        prompt_for_estimate = _extract_prompt_from_messages(messages)
        estimated_request_tokens = _estimate_request_tokens(
            prompt_for_estimate,
            int(body.get("max_tokens") or 0),
        )

        # ── Per-org TPM rate limit enforcement (Phase 1 hardening) ──
        # Fail-CLOSED Lua check; org_tpm_limit=0 disables the ceiling.
        if RATE_LIMITER is not None and auth_ctx is not None and auth_ctx.org_slug:
            org_tpm_limit = 0
            if CONFIG_SYNC is not None:
                try:
                    org_cfg = CONFIG_SYNC.get_config(auth_ctx.org_slug) or {}
                    org_tpm_limit = int(org_cfg.get("org_tpm_limit", 0) or 0)
                except Exception:
                    org_tpm_limit = 0
            if org_tpm_limit:
                org_allowed, org_current = await RATE_LIMITER.check_org_rate_limit(
                    auth_ctx.org_slug,
                    org_tpm_limit,
                    estimated_request_tokens,
                )
                if not org_allowed:
                    METRICS["blocked"] += 1
                    _emit_telemetry(
                        status_code=429,
                        event_type="input_blocked",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.30,
                        threat_type="rate_limit_org_tpm",
                        metadata={
                            "detail": f"Org TPM ceiling exceeded ({org_current}/{org_tpm_limit})",
                            "org_slug": auth_ctx.org_slug,
                        },
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "rate_limited",
                            "scope": "org",
                            "message": f"Org TPM ceiling exceeded ({org_current}/{org_tpm_limit}).",
                            "code": "org_rate_limit_exceeded",
                        },
                        headers={"Retry-After": "60"},
                    )

        # ── Per-key TPM rate limit enforcement ──
        if RATE_LIMITER is not None and auth_ctx is not None and rate_limit_tpm:
            rate_allowed, current_usage = await RATE_LIMITER.check_rate_limit(
                auth_ctx.key_hash,
                rate_limit_tpm,
                estimated_request_tokens,
            )
            if not rate_allowed:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=429,
                    event_type="input_blocked",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=0.30,
                    threat_type="rate_limit_tpm",
                    metadata={
                        "detail": (
                            f"Token rate limit exceeded ({current_usage}/{rate_limit_tpm} TPM, "
                            f"est {estimated_request_tokens} tokens this request)"
                        ),
                    },
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": (
                            f"Token rate limit exceeded ({current_usage}/{rate_limit_tpm} TPM, "
                            f"estimated {estimated_request_tokens} tokens for this request)."
                        ),
                        "code": "rate_limit_exceeded",
                        "blocked_by": "rate_limit",
                        "rate_limit": {
                            "scope": "gateway_tpm",
                            "current_tpm": current_usage,
                            "limit_tpm": rate_limit_tpm,
                            "estimated_tokens": estimated_request_tokens,
                        },
                    },
                    headers={"Retry-After": "60"},
                )

        max_ctx = getattr(auth_ctx, "max_context_tokens", 0) if auth_ctx else 0
        if not max_ctx:
            max_ctx = CONFIG.get("default_max_context_tokens", 0)
        if max_ctx > 0:
            messages = minimize_context(messages, max_ctx)
            body["messages"] = messages

        prompt = prompt_for_estimate or _extract_prompt_from_messages(messages)
        _prompt_snippet = prompt[:500] if prompt else ""
        agent_data = _extract_agent_data(body, x_agent_data)

        # ── Input scanning (always runs, even without backend) ──
        effective_prompt = prompt
        redacted_prompt = None
        scan_verdict = None
        hallucination_flagged = False
        output_enforcement = None
        is_rag_request = _detect_rag_request(body, messages)

        # ── Blocked keywords check (from firewall config) ──
        if not firewall_disabled:
            custom_blocked = org_config.get("blocked_keywords", [])
            if custom_blocked and isinstance(custom_blocked, list):
                prompt_lower = prompt.lower()
                matched_kw = [
                    kw for kw in custom_blocked
                    if _blocked_keyword_matches(prompt_lower, kw)
                ]
                if matched_kw:
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        _emit_telemetry(
                            status_code=403,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.70,
                            threat_type="blocked_keyword",
                            metadata={"detail": f"Blocked keyword(s): {', '.join(matched_kw)}"},
                            prompt_snippet=_prompt_snippet,
                            endpoint_id=endpoint_id,
                        )
                        _audit_fire_and_forget(
                            org_slug=org_slug or "",
                            decision="block",
                            rule_code=(matched_kw[0] if matched_kw else "custom_keyword_block"),
                            metadata={
                                "input_bytes": len((prompt or "").encode("utf-8")),
                                "model_id": body.get("model", ""),
                                "matched_keywords": matched_kw,
                            },
                        )
                        return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                            action="block",
                            reason=f"Blocked keyword(s) detected: {', '.join(matched_kw)}",
                            detection_tier="config",
                            threat_type="blocked_keyword",
                            confidence=1.0,
                            matched_patterns=matched_kw,
                            original_prompt=prompt,
                            processing_time_ms=elapsed_ms,
                        ))
                    else:
                        LOG.warning(
                            "MONITOR: blocked keyword(s) found: %s (user=%s)",
                            matched_kw, user_id,
                        )

        # ── Max response tokens enforcement ──
        max_tokens_config = org_config.get("max_response_tokens", 4096)
        if max_tokens_config and not firewall_disabled:
            body["max_tokens"] = min(body.get("max_tokens", max_tokens_config), max_tokens_config)

        if firewall_disabled:
            # Firewall is off -- skip all scanning, route directly to LLM
            is_stream = body.get("stream", False)
            if is_stream:
                METRICS["allowed"] += 1
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="allow",
                    rule_code="firewall_disabled_stream",
                    metadata={"stream": True, "model": body.get("model", "")},
                )
                return _launch_chat_stream_response(
                    request=request,
                    body=body,
                    org_config=org_config,
                    org_slug=org_slug,
                    auth_ctx=auth_ctx,
                    redacted_prompt=None,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_hash=auth_ctx.key_hash if auth_ctx else "",
                    rate_limit_tpm=rate_limit_tpm or 0,
                    estimated_tokens=estimated_request_tokens,
                    org_tpm_limit=int(org_config.get("org_tpm_limit", 0) or 0),
                    secure_output_scan=False,
                )
            code, resp = await LLM_ROUTER.acompletion(body, None)
            METRICS["allowed"] += 1
            elapsed_ms = (time.perf_counter() - start) * 1000
            if code == 200 and isinstance(resp, dict):
                resp["zeroshield"] = _build_zeroshield_metadata(
                    action="passthrough",
                    reason="Firewall disabled. No scanning performed.",
                    detection_tier="none",
                    original_prompt=prompt,
                    processing_time_ms=elapsed_ms,
                )
            return JSONResponse(
                status_code=code if code else 502,
                content=resp if isinstance(resp, dict) else {"error": resp},
            )

        # ── Intent classification ──
        _request_intent = ""
        try:
            from scanner import classify_intent
            _request_intent = classify_intent(effective_prompt)
        except Exception:
            pass

        # ──────────────────────────────────────────────────────────────────
        # POLICY-FIRST PIPELINE
        # ──────────────────────────────────────────────────────────────────
        # The deterministic policy decision runs BEFORE Tier-1/Tier-2 input
        # scanning. This means:
        #   - action=="block"           → request short-circuits; expensive
        #                                 Tier-2 Bedrock scan is SKIPPED.
        #   - action=="redact"          → effective_prompt is replaced with
        #                                 the redacted text; the scanner sees
        #                                 only the redacted prompt.
        #   - action=="rewrite"         → effective_prompt is wrapped with a
        #                                 policy notice; scanner runs on it.
        #   - action=="model_downgrade" → body["model"] is downgraded; scan
        #                                 still runs on the prompt.
        #   - action=="allow"/"monitor" → fall through to scanning unchanged.
        # ──────────────────────────────────────────────────────────────────
        if AGENT_ID and CONFIG["backend_url"]:
            # Optional: backend security scan first (high-certainty threats)
            if CONFIG.get("call_security_scan"):
                code, scan_resp = await asyncio.to_thread(_security_scan, prompt, "")
                if code == 200 and isinstance(scan_resp, dict):
                    action = scan_resp.get("recommended_action") or ""
                    if action in ("block_immediately", "block_and_alert"):
                        METRICS["blocked"] += 1
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                            action="block",
                            reason="Request blocked by backend security scan (high-certainty threat detected).",
                            detection_tier="policy",
                            original_prompt=prompt,
                            processing_time_ms=elapsed_ms,
                        ))

            # Policy check (deterministic, includes backend pattern policies)
            code, check_resp = await asyncio.to_thread(
                _policy_check_cached,
                effective_prompt,
                "",
                user_id,
                endpoint_id,
                agent_data,
                project_id,
                risk_score,
                requested_model,
                org_slug,
                auth_ctx.prefix if auth_ctx else "",
                getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
            )
            if code != 200:
                METRICS["blocked"] += 1
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": "Policy evaluation unavailable. Request denied (fail-closed).",
                        "code": "policy_unavailable",
                    },
                )

            action = check_resp.get("action") or "allow"
            if check_resp.get("event_id"):
                _policy_audit_event_id = check_resp.get("event_id")
            if action == "block":
                has_policy_match = bool(
                    check_resp.get("matched_policies")
                    or check_resp.get("matched_rules")
                    or check_resp.get("matched_policy_names")
                )
                if not has_policy_match:
                    return _ambiguous_policy_block_response(org_slug=org_slug or "")
            if action == "block":
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    categories = check_resp.get("matched_policy_categories") or []
                    threat_type = _category_to_threat_type(categories[0]) if categories else "policy_violation"
                    if not check_resp.get("event_id"):
                        _emit_telemetry(
                            status_code=403,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.85,
                            threat_type=threat_type,
                            metadata={
                                "detail": check_resp.get("message") or "Policy block",
                                "matched_policies": check_resp.get("matched_policies") or [],
                                "matched_rules": check_resp.get("matched_rules") or [],
                            },
                            prompt_snippet=_prompt_snippet,
                            endpoint_id=endpoint_id,
                        )
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="block",
                        rule_code=((check_resp.get("matched_rules") or ["policy_block"])[0]),
                        metadata={
                            "input_bytes": len((prompt or "").encode("utf-8")),
                            "model_id": body.get("model", ""),
                            "matched_rules": check_resp.get("matched_rules") or [],
                            "matched_policies": check_resp.get("matched_policies") or [],
                        },
                    )
                    return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                        action="block",
                        reason=check_resp.get("message") or "Request blocked by policy engine.",
                        detail=check_resp.get("message") or "Request blocked by policy engine.",
                        detection_tier="policy",
                        threat_type=threat_type,
                        matched_patterns=check_resp.get("matched_rules") or check_resp.get("matched_policies") or [],
                        original_prompt=prompt,
                        processing_time_ms=elapsed_ms,
                    ))
                else:
                    LOG.warning(
                        "MONITOR: policy would block request (message=%s, user=%s)",
                        check_resp.get("message"), user_id,
                    )

            if action == "redact" and check_resp.get("redacted_prompt"):
                effective_prompt = check_resp["redacted_prompt"]
                redacted_prompt = effective_prompt

            # ── Rewrite action: strip harmful pattern, log original ──
            if action == "rewrite":
                original_effective = effective_prompt
                matched_rules = check_resp.get("matched_rules") or []
                rewrite_detail = check_resp.get("message") or "Content policy applied"
                effective_prompt = f"[Content policy applied: harmful content removed] {effective_prompt}"
                redacted_prompt = effective_prompt
                _gateway_lifecycle_telemetry_emitted = True
                _emit_telemetry(
                        event_type="input_blocked",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="rewrite",
                        risk_score=0.5,
                        threat_type="policy_violation",
                        pipeline_stage="query",
                        intent=_request_intent,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        metadata={"detail": rewrite_detail, "original_prompt": original_effective[:500]},
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="rewrite",
                    rule_code=((matched_rules or ["policy_rewrite"])[0]),
                    metadata={
                        "input_bytes": len((prompt or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "matched_rules": matched_rules,
                    },
                )
                LOG.info("Rewrite action applied (user=%s, rules=%s)", user_id, matched_rules)

            # ── Model downgrade action: switch to cheaper/safer model ──
            if action == "model_downgrade":
                redaction_cfg = check_resp.get("redaction_config") or {}
                downgrade_to = redaction_cfg.get("downgrade_to", org_config.get("litellm_default_model", "gpt-3.5-turbo"))
                original_model = requested_model
                requested_model = downgrade_to
                body["model"] = downgrade_to
                _emit_telemetry(
                        event_type="input_blocked",
                        model=original_model,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="model_downgrade",
                        risk_score=0.4,
                        threat_type="policy_violation",
                        pipeline_stage="query",
                        intent=_request_intent,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        metadata={"detail": f"Downgraded from {original_model} to {downgrade_to}"},
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="downgrade",
                    rule_code=((check_resp.get("matched_rules") or ["policy_downgrade"])[0]),
                    metadata={
                        "input_bytes": len((prompt or "").encode("utf-8")),
                        "model_id": original_model,
                        "downgrade_to": downgrade_to,
                    },
                )
                LOG.info("Model downgrade: %s -> %s (user=%s)", original_model, downgrade_to, user_id)

        if INPUT_SCANNER is not None and org_config.get("input_scan_enabled", True):
            tier2_execution_mode = str(org_config.get("tier2_execution_mode", "sync_pre_llm")).strip().lower()
            tier2_stream_hold_enabled = bool(org_config.get("tier2_stream_hold_enabled", False))
            is_stream_request = bool(body.get("stream", False))
            force_sync_tier2 = tier2_execution_mode == "sync_pre_llm"

            # Async-post mode still needs synchronous Tier-2 for strict blocking
            # semantics when we cannot hold streaming safely.
            if tier2_execution_mode == "async_post_llm" and enforcement_mode == "block":
                if not is_stream_request:
                    force_sync_tier2 = True
                elif tier2_stream_hold_enabled:
                    force_sync_tier2 = True
                else:
                    force_sync_tier2 = True
                    LOG.warning(
                        "tier2_execution_mode=async_post_llm with block+stream without hold; forcing sync tier2"
                    )

            scan_start = time.perf_counter()
            # Phase 0 D-G2-v3: per-org tri-state Tier-2 override. Use .get()
            # without a default so ``None`` (no per-org opinion) survives and
            # is distinguishable from explicit ``False`` (force-disable).
            org_tier2_override = org_config.get("tier2_enabled")
            # Phase 0 D-G3-v3: per-org Tier-2 strictness governs behaviour
            # when the Bedrock circuit breaker is OPEN. Default True (fail
            # closed with HTTP 451) per Adversarial Triage F5.
            org_tier2_strict = bool(org_config.get("tier2_strict", True))
            # Per-org toxicity threshold (passed by value, never stored on the
            # shared scanner singleton, so concurrent orgs cannot race on it).
            org_toxicity_threshold = org_config.get("toxicity_threshold")
            try:
                if force_sync_tier2:
                    verdict = await INPUT_SCANNER.scan_prompt_with_tier2(
                        effective_prompt,
                        is_rag=is_rag_request,
                        org_tier2_override=org_tier2_override,
                        org_slug=org_slug or "",
                        org_tier2_strict=org_tier2_strict,
                        toxicity_threshold=org_toxicity_threshold,
                    )
                    if verdict and verdict.tier == "tier_2":
                        stage_metrics["tier2_ms"] = round((time.perf_counter() - scan_start) * 1000, 2)
                    else:
                        stage_metrics["tier1_ms"] = round((time.perf_counter() - scan_start) * 1000, 2)
                else:
                    verdict = await INPUT_SCANNER.scan_prompt(
                        effective_prompt, is_rag=is_rag_request,
                        toxicity_threshold=org_toxicity_threshold,
                        org_slug=org_slug,
                    )
                    stage_metrics["tier1_ms"] = round((time.perf_counter() - scan_start) * 1000, 2)
                    await enqueue_job(
                        job_type="tier2_post_scan",
                        request_id=request.headers.get("X-Request-ID", f"zs-tier2-{_uuid.uuid4().hex[:12]}"),
                        org_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                        payload={
                            "request_id": request.headers.get("X-Request-ID", ""),
                            "organization_id": getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                            "user_id": user_id,
                            "project_id": str(project_id or ""),
                            "prompt": effective_prompt,
                            "execution_mode": tier2_execution_mode,
                            "is_rag": bool(is_rag_request),
                            "is_stream": bool(is_stream_request),
                        },
                    )
            except Tier2UnavailableStrict as _t2err:
                # G3 breaker OPEN + tier2_strict=True. Return HTTP 451 with
                # the standardized degraded envelope and ``Retry-After``.
                METRICS["blocked"] += 1
                retry_after = int(_t2err.retry_after_seconds)
                return JSONResponse(
                    status_code=451,
                    content={
                        "status": "degraded",
                        "reason": "tier2_unavailable_strict",
                        "retry_after": retry_after,
                        "detail": (
                            "Tier-2 scanner circuit breaker is open; refusing request "
                            "while strict mode is enabled."
                        ),
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            scan_verdict = verdict
            tier2_fail_closed_enabled = org_config.get("tier2_fail_closed_enabled", True)

            if _is_tier2_degraded_verdict(verdict) and tier2_fail_closed_enabled:
                LOG.warning(
                    "Tier-2 scanner degraded (reason=%s, detail=%s, user=%s)",
                    getattr(verdict, "reason_code", ""),
                    verdict.detail,
                    user_id,
                )
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _emit_telemetry(
                        status_code=403,
                        event_type="input_blocked",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=getattr(verdict, "confidence", None) or 0.85,
                        threat_type=getattr(verdict, "threat_type", None) or "tier2_degraded",
                        compliance_tags=org_config.get("compliance_frameworks", []),
                        pipeline_stage="query",
                        intent=_request_intent,
                        latency_ms=elapsed_ms,
                        metadata={
                            "detail": verdict.detail,
                            "reason_code": getattr(verdict, "reason_code", ""),
                            "matched_patterns": getattr(verdict, "matched_patterns", None) or [],
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="block",
                        rule_code="tier2_degraded",
                        metadata={
                            "input_bytes": len((prompt or "").encode("utf-8")),
                            "model_id": body.get("model", ""),
                            "reason_code": getattr(verdict, "reason_code", ""),
                        },
                    )
                    return _build_block_response(
                        403,
                        "tier2_degraded",
                        _build_zeroshield_metadata(
                            action="block",
                            reason="Tier-2 Bedrock scanner degraded; fail-closed policy blocked the request.",
                            detection_tier=verdict.tier,
                            threat_type=verdict.threat_type,
                            confidence=verdict.confidence,
                            matched_patterns=verdict.matched_patterns,
                            original_prompt=prompt,
                            detail=verdict.detail,
                            processing_time_ms=elapsed_ms,
                        ),
                        stage_metrics=stage_metrics,
                        prompt=prompt,
                        route_metadata=route_metadata,
                        requested_model=body.get("model", ""),
                        scan_verdict=verdict,
                    )
                LOG.warning(
                    "MONITOR: Tier-2 degraded request allowed due to enforcement_mode=%s (user=%s)",
                    enforcement_mode,
                    user_id,
                )

            injection_threshold = org_config.get("prompt_injection_threshold", 0.80)
            _INJECTION_THREAT_TYPES = {"prompt_injection", "jailbreak", "goal_hijacking"}
            _is_injection = verdict.threat_type in _INJECTION_THREAT_TYPES
            _should_block_verdict = (
                verdict.action == "block"
                and verdict.threat_type not in ("pii", "secret")
            )
            if _is_injection:
                # Injection-type threats: respect the scan_block_on_injection toggle
                # and the prompt_injection_threshold confidence gate
                _should_block_verdict = (
                    _should_block_verdict
                    and org_config.get("scan_block_on_injection", True)
                    and verdict.confidence >= injection_threshold
                )
            # For non-injection threats (toxicity, dos, etc.), the scanner
            # already applied its own threshold; respect the verdict directly.
            if _should_block_verdict:
                LOG.warning(
                    "Input blocked by scanner (type=%s, detail=%s, user=%s)",
                    verdict.threat_type, verdict.detail, user_id,
                )
                _emit_telemetry(
                    status_code=403 if enforcement_mode == "block" else 200,
                    event_type="input_blocked",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block" if enforcement_mode == "block" else "monitor",
                    risk_score=verdict.confidence,
                    threat_type=verdict.threat_type,
                    compliance_tags=org_config.get("compliance_frameworks", []),
                    pipeline_stage="query",
                    intent=_request_intent,
                    metadata={
                        "detail": verdict.detail,
                        "confidence": verdict.confidence,
                        "matched_patterns": verdict.matched_patterns,
                        **_telemetry_owasp_metadata(verdict.threat_type, verdict=verdict),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                _maybe_emit_critical_alert(
                    threat_type=verdict.threat_type,
                    confidence=verdict.confidence,
                    user_id=user_id,
                    detail=verdict.detail,
                    org_slug=org_slug or "",
                )
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    tier_label = {"tier_1": "Tier 1 regex", "tier_1_5": "Tier 1.5 fuzzy", "tier_1_6": "Tier 1.6 semantic", "tier_2": "Tier 2 ML"}.get(verdict.tier, verdict.tier)
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="block",
                        rule_code=f"{verdict.tier or 'tier_1'}_{verdict.threat_type}",
                        metadata={
                            "input_bytes": len((prompt or "").encode("utf-8")),
                            "model_id": body.get("model", ""),
                            "score": verdict.confidence,
                            "matched_patterns": verdict.matched_patterns,
                        },
                    )
                    return _build_block_response(
                        403,
                        "content_blocked",
                        _build_zeroshield_metadata(
                            action="block",
                            reason=f"{tier_label} scanner detected {verdict.threat_type}: {verdict.detail}",
                            detection_tier=verdict.tier,
                            threat_type=verdict.threat_type,
                            confidence=verdict.confidence,
                            matched_patterns=verdict.matched_patterns,
                            original_prompt=prompt,
                            processing_time_ms=elapsed_ms,
                            intent=_request_intent,
                        ),
                        stage_metrics=stage_metrics,
                        prompt=prompt,
                        route_metadata=route_metadata,
                        requested_model=body.get("model", ""),
                        scan_verdict=verdict,
                    )
                else:
                    LOG.warning(
                        "MONITOR: would block %s (confidence=%.2f, user=%s)",
                        verdict.threat_type, verdict.confidence, user_id,
                    )

            # pii_detection_enabled (mapped to scan_block_on_pii in the gateway
            # payload) is org-scoped. When an org disables PII detection we skip
            # PII redaction for that org. Secrets/credentials are ALWAYS redacted
            # regardless of this toggle.
            pii_detection_enabled = bool(org_config.get("scan_block_on_pii", True))
            _redact_threat = verdict.threat_type in ("pii", "secret") and (
                verdict.threat_type == "secret" or pii_detection_enabled
            )

            if verdict.action in ("block", "redact") and _redact_threat:
                LOG.info(
                    "PII/secret detected, redacting before LLM call (type=%s, patterns=%s, user=%s)",
                    verdict.threat_type, verdict.matched_patterns, user_id,
                )
                _maybe_emit_critical_alert(
                    threat_type=verdict.threat_type,
                    confidence=verdict.confidence,
                    user_id=user_id,
                    detail=f"PII/secret detected and redacted: {', '.join(verdict.matched_patterns)}",
                    org_slug=org_slug or "",
                )
                effective_prompt = INPUT_SCANNER.redact_pii(effective_prompt)
                redacted_prompt = effective_prompt
            elif (
                verdict.threat_type == "pii"
                and not pii_detection_enabled
                and verdict.action in ("block", "redact", "flag")
            ):
                LOG.info(
                    "PII detected but org has PII detection disabled; allowing prompt unredacted (user=%s)",
                    user_id,
                )

            if verdict.action == "flag" and _redact_threat:
                LOG.info("PII/secret flagged in prompt, redacting before LLM call (user=%s)", user_id)
                effective_prompt = INPUT_SCANNER.redact_pii(effective_prompt)
                redacted_prompt = effective_prompt

        if not AGENT_ID or not CONFIG["backend_url"]:
            # No backend: forward to LLM (input scanning already done above)
            is_stream = body.get("stream", False)
            if is_stream:
                METRICS["allowed"] += 1
                _tel_action = "redact" if redacted_prompt is not None else "allow"
                _tel_threat = (scan_verdict.threat_type if scan_verdict and redacted_prompt is not None else "")
                _tel_risk = (scan_verdict.confidence if scan_verdict and redacted_prompt is not None else 0.0)
                telemetry_start = time.perf_counter()
                if _policy_audit_event_id is None and not _gateway_lifecycle_telemetry_emitted:
                    _emit_telemetry(
                        event_type="request",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        latency_ms=(time.perf_counter() - start) * 1000,
                        risk_score=_tel_risk,
                        action=_tel_action,
                        threat_type=_tel_threat,
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                        metadata={"stage_metrics_ms": stage_metrics},
                    )
                stage_metrics["telemetry_enqueue_ms"] = round((time.perf_counter() - telemetry_start) * 1000, 2)
                preflight_block = _stream_preflight_block_if_needed(org_config)
                if preflight_block is not None:
                    return preflight_block
                return _launch_chat_stream_response(
                    request=request,
                    body=body,
                    org_config=org_config,
                    org_slug=org_slug,
                    auth_ctx=auth_ctx,
                    redacted_prompt=redacted_prompt,
                    scan_verdict=scan_verdict,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_hash=auth_ctx.key_hash if auth_ctx else "",
                    rate_limit_tpm=rate_limit_tpm or 0,
                    estimated_tokens=estimated_request_tokens,
                    org_tpm_limit=int(org_config.get("org_tpm_limit", 0) or 0),
                    secure_output_scan=bool(CONFIG.get("output_scan_enabled", True)),
                )
            upstream_start = time.perf_counter()
            code, resp = await LLM_ROUTER.acompletion(body, redacted_prompt)
            stage_metrics["upstream_ms"] = round((time.perf_counter() - upstream_start) * 1000, 2)
            METRICS["allowed"] += 1
            if code == 200:
                elapsed_ms = (time.perf_counter() - start) * 1000
                _tel_action = "redact" if redacted_prompt is not None else "allow"
                _tel_threat = (scan_verdict.threat_type if scan_verdict and redacted_prompt is not None else "")
                _tel_risk = (scan_verdict.confidence if scan_verdict and redacted_prompt is not None else 0.0)
                telemetry_start = time.perf_counter()
                if _policy_audit_event_id is None and not _gateway_lifecycle_telemetry_emitted:
                    _emit_telemetry(
                        event_type="request",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        latency_ms=elapsed_ms,
                        risk_score=_tel_risk,
                        action=_tel_action,
                        threat_type=_tel_threat,
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                        metadata={"stage_metrics_ms": stage_metrics},
                    )
                stage_metrics["telemetry_enqueue_ms"] = round((time.perf_counter() - telemetry_start) * 1000, 2)
                response_headers: dict[str, str] = {}
                if isinstance(resp, dict):
                    if redacted_prompt is not None:
                        resp["zeroshield"] = _build_zeroshield_metadata(
                            action="redact",
                            reason=f"PII detected in prompt ({', '.join(scan_verdict.matched_patterns if scan_verdict else [])}). Redacted before forwarding to LLM.",
                            detection_tier=scan_verdict.tier if scan_verdict else "tier_1",
                            threat_type=scan_verdict.threat_type if scan_verdict else "pii",
                            confidence=scan_verdict.confidence if scan_verdict else 0.85,
                            matched_patterns=scan_verdict.matched_patterns if scan_verdict else [],
                            original_prompt=prompt,
                            redacted_prompt=redacted_prompt,
                            detail=scan_verdict.detail if scan_verdict else "",
                            processing_time_ms=elapsed_ms,
                        )
                        response_headers["X-ZeroShield-Action"] = "redacted"
                    else:
                        zs_action, zs_reason, zs_threat, zs_conf, zs_patterns, zs_detail = _resolve_success_metadata_from_verdict(scan_verdict)
                        resp["zeroshield"] = _build_zeroshield_metadata(
                            action=zs_action,
                            reason=zs_reason,
                            detection_tier=scan_verdict.tier if scan_verdict else "none",
                            threat_type=zs_threat,
                            confidence=zs_conf,
                            matched_patterns=zs_patterns,
                            original_prompt=prompt,
                            detail=zs_detail,
                            processing_time_ms=elapsed_ms,
                        )
                        if zs_action == "flag":
                            response_headers["X-ZeroShield-Action"] = "flag"
                # ── SECURITY FIX: Redact sensitive fields from zeroshield metadata before returning to client ──
                if isinstance(resp.get("zeroshield"), dict):
                    resp["zeroshield"] = _redact_for_client_response(resp["zeroshield"]) or {}
                return JSONResponse(content=resp, headers=response_headers)
            return JSONResponse(
                status_code=code if code else 502,
                content=resp if isinstance(resp, dict) else {"error": resp},
            )

        # ── Policy check + backend security scan run ABOVE input_scan now ──
        # (See block before `if INPUT_SCANNER is not None ...` earlier in this handler.)
        # Policy decisions deterministically run BEFORE Tier-1/Tier-2 scanning so:
        #   - action == "block" short-circuits the request (Tier-2 Bedrock cost saved)
        #   - action == "redact" feeds the redacted prompt into the scanner
        #   - action == "rewrite" / "model_downgrade" mutate effective_prompt/body
        # before scanning observes them.

        # ── Dynamic model routing for every /v1/chat/completions request ──
        routing_prefs = _extract_chat_routing_preferences(body, org_config, auth_ctx, scan_verdict)
        routing_active = bool(
            inference_models
            and LLM_ROUTER is not None
            and routing_prefs["routing_enabled"]
            and not isolation_reroute_locked
        )
        if routing_active:
            selection = await LLM_ROUTER.adjudicate_model_selection(
                routing_models=inference_models,
                request_messages=body.get("messages") or [],
                preferred_model=routing_prefs["preferred_model"],
                request_risk_score=routing_prefs["request_risk_score"],
                required_compliance=routing_prefs["required_compliance"],
                data_sensitivity=routing_prefs["data_sensitivity"],
                estimated_tokens=routing_prefs["estimated_tokens"],
                latency_budget_ms=routing_prefs["latency_budget_ms"],
                weights=routing_prefs["weights"],
                allowed_models=routing_allowed_models,
                token_budget_tpm=routing_prefs["token_budget_tpm"],
                adjudicator_model=os.getenv("ZEROSHIELD_GUARD_MODEL_NAME", "zeroshield-guard-120b"),
            )
            if selection:
                route_selection = selection
                requested_model = selection.model_name
                body["model"] = requested_model
                route_metadata = _build_routing_metadata(
                    selection,
                    routing_enabled=bool(routing_prefs["routing_enabled"]),
                    routing_override=routing_prefs["routing_override"],
                    org_routing_enabled=bool(routing_prefs["org_routing_enabled"]),
                    latency_budget_ms=routing_prefs["latency_budget_ms"],
                    data_sensitivity=routing_prefs["data_sensitivity"],
                    required_compliance=routing_prefs["required_compliance"],
                    weights=routing_prefs["weights"],
                    request_risk_score=routing_prefs["request_risk_score"],
                    estimated_tokens=routing_prefs["estimated_tokens"],
                    token_budget_tpm=routing_prefs["token_budget_tpm"],
                )
                LOG.info(
                    "Chat routing selected model: %s (requested=%s, source=%s, reason=%s)",
                    selection.model_name,
                    selection.requested_model or "auto",
                    selection.decision_source,
                    selection.reason,
                )
                # Routing is merged into the terminal request telemetry event so one
                # simulator/gateway activity increments dashboards by 1 (dev parity).
        else:
            route_metadata = {
                "original_model": body.get("model") or "auto",
                "selected_model": body.get("model") or "auto",
                "routed_model": body.get("model") or "auto",
                "routing_enabled": bool(routing_prefs["routing_enabled"]),
                "routing_override": routing_prefs["routing_override"],
                "org_routing_enabled": bool(routing_prefs["org_routing_enabled"]),
                "decision_source": "routing_disabled" if not routing_prefs["routing_enabled"] else "no_routing_models",
                "routing_reason": (
                    "Dynamic routing disabled by governance setting"
                    if not routing_prefs["routing_enabled"]
                    else "No eligible routing models configured; preferred model used"
                ),
                "policy_summary": (
                    "Routing bypassed by explicit disable control"
                    if not routing_prefs["routing_enabled"]
                    else "Routing skipped because model catalog is empty"
                ),
                "compliance_requirements": routing_prefs["required_compliance"],
                "data_sensitivity": routing_prefs["data_sensitivity"],
                "request_risk_score": routing_prefs["request_risk_score"],
                "estimated_tokens": routing_prefs["estimated_tokens"],
                "token_budget_tpm": routing_prefs["token_budget_tpm"],
                "latency_budget_ms": routing_prefs["latency_budget_ms"],
                "weights": routing_prefs["weights"],
            }

        if _is_routing_sentinel_model(requested_model):
            resolved = _resolve_routing_hint_model(
                requested_model, org_config, inference_models
            )
            if resolved and not _is_routing_sentinel_model(resolved):
                requested_model = resolved
                body["model"] = resolved

        routing_probe_only = not needs_inference
        if routing_probe_only and _is_routing_sentinel_model(requested_model):
            if not inference_models:
                return _build_no_inference_provider_response()
            if route_selection is None:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "routing_target_unresolved",
                        "message": (
                            "Could not resolve a routing target. Connect an inference model "
                            "under Model Connection (with API key), enable Dynamic Routing, and "
                            "set Default Fallback Model in Routing Governance."
                        ),
                        "code": "routing_target_unresolved",
                        "blocked_by": "model_routing",
                        "category": "inference_not_configured",
                        "zeroshield": {"routing": route_metadata or {}},
                    },
                )

        # ── Final allowlist enforcement against the routed model ──
        if (
            not routing_probe_only
            and allowed_models
            and requested_model
            and not _is_routing_sentinel_model(requested_model)
            and requested_model not in allowed_models
        ):
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Final routed model '{requested_model}' is not in your allowlist.",
                    "code": "model_not_allowed",
                    "zeroshield": {
                        "routing": route_metadata or {},
                    },
                },
            )

        if not firewall_disabled and org_config.get("model_isolation_enabled", False):
            global_allowed = global_allowed_models
            if (
                global_allowed
                and requested_model
                and not _is_routing_sentinel_model(requested_model)
                and requested_model not in global_allowed
                and not routing_probe_only
            ):
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "forbidden",
                            "message": f"Final routed model '{requested_model}' is not in the global allowlist.",
                            "code": "model_not_allowed",
                            "zeroshield": {
                                "routing": route_metadata or {},
                            },
                        },
                    )
                LOG.warning("MONITOR: routed model '%s' not in global allowlist", requested_model)

        # ── Per-model rate limiting ──
        if RATE_LIMITER is not None and CONFIG_SYNC is not None:
            routing_models = CONFIG_SYNC.get_model_routing(org_slug)
            model_cfg = next((m for m in routing_models if m.get("model_name") == requested_model), None) if routing_models else None
            if model_cfg and model_cfg.get("rate_limit_rpm", 0) > 0:
                model_allowed, model_count = await RATE_LIMITER.check_model_rate_limit(
                    requested_model, model_cfg["rate_limit_rpm"], org_slug=org_slug
                )
                try:
                    from .metrics import record_rate_limit as _prom_rl
                except ImportError:
                    from metrics import record_rate_limit as _prom_rl  # type: ignore[no-redef]
                _prom_rl(org_slug, requested_model, model_allowed)
                if not model_allowed:
                    METRICS["blocked"] += 1
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "rate_limit_exceeded",
                            "message": f"Per-model rate limit exceeded for {requested_model}.",
                            "code": "model_rate_limit",
                        },
                        headers={"Retry-After": "60"},
                    )

        # ── Optional: backend deep scan (Tier 2, async parallel) ──
        deep_scan_task = None

        def _cancel_deep_scan() -> None:
            nonlocal deep_scan_task
            if deep_scan_task is not None and not deep_scan_task.done():
                deep_scan_task.cancel()
                deep_scan_task = None

        if org_config.get("deep_scan_enabled") and org_config.get("input_scan_enabled", True) and AGENT_ID and CONFIG["backend_url"]:
            deep_scan_task = asyncio.create_task(
                asyncio.to_thread(_security_scan, effective_prompt, "")
            )

        is_stream = body.get("stream", False)

        if auth_ctx is not None and needs_inference:
            inference_err = _validate_org_inference_model(
                requested_model=requested_model,
                body=body,
                inference_models=inference_models,
            )
            if inference_err is not None:
                _cancel_deep_scan()
                return inference_err
            body["_inference_allowlist"] = sorted(_routing_identity_set(inference_models))
            if route_selection and route_selection.fallback_chain:
                body["_compliant_fallback_chain"] = list(route_selection.fallback_chain)
            elif CONFIG_SYNC is not None:
                try:
                    from routing_isolation import fallback_profile_key
                except ImportError:
                    from .routing_isolation import fallback_profile_key
                fb_payload = CONFIG_SYNC.get_fallback_chains(org_slug)
                profile_key = fallback_profile_key(
                    routing_prefs.get("data_sensitivity", "public"),
                    routing_prefs.get("required_compliance"),
                )
                profile_chain = (fb_payload.get("chains") or {}).get(profile_key, [])
                per_primary = (fb_payload.get("per_primary") or {}).get(requested_model)
                body["_compliant_fallback_chain"] = per_primary or [
                    m for m in profile_chain if m != requested_model
                ]

        # ── Circuit breaker check ──
        if CIRCUIT_BREAKER is not None:
            cb_status = await CIRCUIT_BREAKER.check(requested_model)
            if cb_status.should_block:
                METRICS["blocked"] += 1
                _cancel_deep_scan()
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": f"Circuit breaker OPEN for model '{requested_model}'. Try again later.",
                        "code": "circuit_breaker_open",
                    },
                    headers={"Retry-After": str(CONFIG.get("circuit_breaker_cooldown_seconds", 120))},
                )

        if is_stream:
            _cancel_deep_scan()
            preflight_block = _stream_preflight_block_if_needed(org_config)
            if preflight_block is not None:
                return preflight_block
            if route_selection is not None:
                requested = getattr(route_selection, "requested_model", None) or "auto"
                routed = getattr(route_selection, "model_name", "") or ""
                rerouted = bool(requested and requested != "auto" and routed and routed != requested)
                if rerouted:
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="downgrade",
                        rule_code="model_routed_stream",
                        metadata={
                            "stream": True,
                            "selected_model": routed,
                            "requested_model": requested,
                            "decision_source": getattr(route_selection, "decision_source", ""),
                        },
                    )
            METRICS["allowed"] += 1
            return _launch_chat_stream_response(
                request=request,
                body=body,
                org_config=org_config,
                org_slug=org_slug,
                auth_ctx=auth_ctx,
                redacted_prompt=redacted_prompt,
                route_selection=route_selection,
                scan_verdict=scan_verdict,
                user_id=user_id,
                project_id=str(project_id or ""),
                key_hash=auth_ctx.key_hash if auth_ctx else "",
                rate_limit_tpm=rate_limit_tpm or 0,
                estimated_tokens=estimated_request_tokens,
                org_tpm_limit=int(org_config.get("org_tpm_limit", 0) or 0),
                secure_output_scan=bool(CONFIG.get("output_scan_enabled", True)),
            )
        upstream_start = time.perf_counter()
        code, llm_resp = await LLM_ROUTER.acompletion(body, redacted_prompt)
        stage_metrics["upstream_ms"] = round((time.perf_counter() - upstream_start) * 1000, 2)
        if code != 200:
            # Record circuit breaker error
            if CIRCUIT_BREAKER is not None and code >= 500:
                await CIRCUIT_BREAKER.record_error(requested_model, org_slug=org_slug or "default")
            # Record risk event for model errors
            if REDIS_CLIENT is not None and code >= 400:
                from model_state import record_risk_event, check_auto_isolate
                risk_val = 0.8 if code >= 500 else 0.4
                composite = await record_risk_event(REDIS_CLIENT, requested_model, org_slug or "default", risk_val, "circuit_breaker")
                # Check auto-isolation threshold
                ms_key = f"model_state:{org_slug or 'default'}:{requested_model}"
                ms_raw = await REDIS_CLIENT.get(ms_key)
                ms_threshold = 80.0
                ms_action = "block"
                if ms_raw:
                    try:
                        ms_data = json.loads(ms_raw if isinstance(ms_raw, str) else ms_raw.decode())
                        ms_threshold = float(ms_data.get("threshold", 80.0))
                        ms_action = ms_data.get("action", "block")
                    except (json.JSONDecodeError, TypeError):
                        pass
                await check_auto_isolate(REDIS_CLIENT, requested_model, org_slug or "default", composite, ms_threshold, ms_action)
            return JSONResponse(
                status_code=code if code else 502,
                content=llm_resp if isinstance(llm_resp, dict) else {"error": llm_resp},
            )

        # Record circuit breaker success
        if CIRCUIT_BREAKER is not None:
            await CIRCUIT_BREAKER.record_success(requested_model, org_slug=org_slug or "default")

        # Optional: post-response policy check
        response_text = _extract_response_from_completion(llm_resp)

        # ── Output guard (non-streaming) ──
        # Respect per-org master toggle (response_filtering_enabled → output_scan_enabled)
        # in addition to the global GATEWAY_OUTPUT_GUARD_ENABLED env default.
        output_verdict = None
        _output_guard_active = (
            OUTPUT_GUARD is not None
            and response_text
            and CONFIG.get("output_guard_enabled", True)
            and org_config.get("output_scan_enabled", CONFIG.get("output_scan_enabled", True))
        )
        if _output_guard_active:
            # Hallucination grounding must score the answer against ACTUAL
            # retrieved RAG context — never against the user's own prompt.
            # Previously context_chunks was seeded from the request's own
            # system/user messages, so every benign non-RAG answer scored
            # grounding≈0 (the answer necessarily introduces new tokens absent
            # from the question) and was rewritten into a "cannot verify"
            # refusal. Populate context ONLY from real RAG retrieval; with no
            # retrieval the list stays empty and score_hallucination() takes
            # its safe no-context branch (grounding_score=1.0).
            context_chunks: list[str] = []
            # RAG context binding: enrich grounding with retrieved documents
            rag_context_id = request.headers.get("X-ZeroShield-RAG-Context-ID", "")
            if rag_context_id and REDIS_CLIENT is not None:
                try:
                    import json as _json_ctx
                    raw = await REDIS_CLIENT.get(rag_context_id)
                    if raw:
                        rag_chunks = _json_ctx.loads(raw if isinstance(raw, str) else raw.decode())
                        context_chunks.extend(rag_chunks)
                except Exception:
                    pass  # fail-open: context binding is best-effort
            output_verdict = await OUTPUT_GUARD.inspect(
                response_text,
                context_chunks=context_chunks,
                org_config=(CONFIG_SYNC.get_config(org_slug) if (CONFIG_SYNC is not None and org_slug) else None),
                org_slug=org_slug or "",
            )
            # Capture raw output before any redaction for pipeline visibility
            _raw_model_output = response_text[:500] if response_text else ""
            # §1.7 incident-logging control: when output_incident_logging_enabled is
            # false, non-blocking output-guard actions (redact/flag/rewrite) skip
            # telemetry + audit. Hard blocks always log (and the control-plane
            # compliance floor forces this on under strict frameworks).
            _output_incident_logging = bool(org_config.get("output_incident_logging_enabled", True))

            def _emit_output_incident_telemetry(**_tel_kwargs):
                if _output_incident_logging:
                    _emit_telemetry(**_tel_kwargs)

            def _audit_output_incident(**_audit_kwargs):
                if _output_incident_logging:
                    _audit_fire_and_forget(**_audit_kwargs)

            if output_verdict.action == "block":
                METRICS["blocked"] += 1
                # Record risk event for output guard block
                if REDIS_CLIENT is not None:
                    from model_state import record_risk_event, check_auto_isolate
                    composite = await record_risk_event(REDIS_CLIENT, body.get("model", ""), org_slug or "default", getattr(output_verdict, 'confidence', 0.90), "output_guard")
                    ms_key = f"model_state:{org_slug or 'default'}:{body.get('model', '')}"
                    ms_raw = await REDIS_CLIENT.get(ms_key)
                    ms_threshold = 80.0
                    if ms_raw:
                        try:
                            ms_data = json.loads(ms_raw if isinstance(ms_raw, str) else ms_raw.decode())
                            ms_threshold = float(ms_data.get("threshold", 80.0))
                        except (json.JSONDecodeError, TypeError):
                            pass
                    await check_auto_isolate(REDIS_CLIENT, body.get("model", ""), org_slug or "default", composite, ms_threshold)
                _emit_telemetry(
                    status_code=403,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=getattr(output_verdict, 'confidence', 0.90),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={
                        "detail": output_verdict.detail,
                        "response_snippet": _raw_model_output,
                        "raw_output": _raw_model_output,
                        "sanitized_output": "[BLOCKED]",
                        "guardrail_reasoning": output_verdict.detail,
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        **_telemetry_owasp_metadata(output_verdict.threat_type),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="block",
                    rule_code=f"output_guard_{output_verdict.threat_type}",
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "score": getattr(output_verdict, "confidence", 0.9),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                    },
                )
                return _build_block_response(
                    403,
                    "output_blocked",
                    _build_zeroshield_metadata(
                        action="block",
                        reason=f"Output guard detected {output_verdict.threat_type} in LLM response.",
                        detection_tier="output_guard",
                        threat_type=output_verdict.threat_type,
                        confidence=getattr(output_verdict, "confidence", 0.9),
                        matched_patterns=getattr(output_verdict, "matched_patterns", []),
                        compliance_tags=getattr(output_verdict, "compliance_tags", []),
                        original_prompt=prompt,
                        detail=output_verdict.detail,
                        processing_time_ms=elapsed_ms,
                        security_incident=True,
                    ),
                    stage_metrics=stage_metrics,
                    prompt=prompt,
                    route_metadata=route_metadata,
                    requested_model=body.get("model", ""),
                    scan_verdict=scan_verdict,
                    output_scan_verdict=output_verdict,
                )
            if output_verdict.action == "redact":
                LOG.info("Output redaction triggered (type=%s, user=%s)", output_verdict.threat_type, user_id)
                redacted_response = INPUT_SCANNER.redact_pii(response_text)
                _set_completion_response_text(llm_resp, redacted_response)
                response_text = redacted_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "redact",
                        "reason": f"Output guard redacted {output_verdict.threat_type or 'unsafe'} content before delivery.",
                        "detail": output_verdict.detail,
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.70),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "redacted_response": redacted_response,
                    },
                )
                _emit_output_incident_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="redact",
                    risk_score=getattr(output_verdict, 'confidence', 0.70),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={
                        "detail": output_verdict.detail,
                        "response_snippet": _raw_model_output,
                        "raw_output": _raw_model_output,
                        "sanitized_output": response_text[:500] if response_text else "",
                        "guardrail_reasoning": output_verdict.detail,
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        **_telemetry_owasp_metadata(output_verdict.threat_type),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                _audit_output_incident(
                    org_slug=org_slug or "",
                    decision="rewrite",
                    rule_code=f"output_guard_redact_{output_verdict.threat_type}",
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "score": getattr(output_verdict, "confidence", 0.7),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                    },
                )
            if output_verdict.action == "rewrite":
                LOG.info("Output rewrite triggered (type=%s, user=%s)", output_verdict.threat_type, user_id)
                rewritten_response = _rewrite_output_response_text(output_verdict.threat_type, output_verdict.detail)
                _set_completion_response_text(llm_resp, rewritten_response)
                response_text = rewritten_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "rewrite",
                        "reason": f"Output guard rewrote {output_verdict.threat_type or 'unsafe'} content before delivery.",
                        "detail": output_verdict.detail,
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.65),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "rewritten_response": rewritten_response,
                    },
                )
                _emit_output_incident_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="rewrite",
                    risk_score=getattr(output_verdict, 'confidence', 0.65),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={
                        "detail": output_verdict.detail,
                        "response_snippet": _raw_model_output,
                        "raw_output": _raw_model_output,
                        "sanitized_output": response_text[:500] if response_text else "",
                        "guardrail_reasoning": output_verdict.detail,
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        **_telemetry_owasp_metadata(output_verdict.threat_type),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                _audit_output_incident(
                    org_slug=org_slug or "",
                    decision="rewrite",
                    rule_code=f"output_guard_rewrite_{output_verdict.threat_type}",
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "score": getattr(output_verdict, "confidence", 0.65),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                    },
                )
            if output_verdict.action == "flag":
                if output_verdict.threat_type == "hallucination":
                    hallucination_flagged = True
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "flag",
                        "reason": f"Output guard flagged {output_verdict.threat_type or 'unsafe'} content for review.",
                        "detail": output_verdict.detail,
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.60),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "review_required": True,
                        "factuality_warning": output_verdict.threat_type == "hallucination",
                    },
                )
                _emit_output_incident_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="flag",
                    risk_score=getattr(output_verdict, 'confidence', 0.60),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={"detail": output_verdict.detail, "response_snippet": _raw_model_output, "raw_output": _raw_model_output, "sanitized_output": response_text[:500] if response_text else "", "guardrail_reasoning": output_verdict.detail, "matched_patterns": getattr(output_verdict, 'matched_patterns', [])},
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
        elif INPUT_SCANNER is not None and response_text and org_config.get("output_scan_enabled", True):
            output_verdict = await INPUT_SCANNER.scan_output(response_text)
            if output_verdict.action == "flag" and output_verdict.threat_type in ("pii", "secret"):
                LOG.info("PII/secret detected in LLM response, redacting (user=%s)", user_id)
                redacted_response = INPUT_SCANNER.redact_pii(response_text)
                _set_completion_response_text(llm_resp, redacted_response)
                response_text = redacted_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "redact",
                        "reason": f"Output scanner redacted {output_verdict.threat_type} content before delivery.",
                        "detail": f"Output redacted: {output_verdict.detail}",
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.60),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "redacted_response": redacted_response,
                    },
                )
                _emit_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="redact",
                    risk_score=getattr(output_verdict, 'confidence', 0.60),
                    threat_type=output_verdict.threat_type,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    compliance_tags=getattr(output_verdict, 'compliance_tags', []),
                    metadata={"detail": f"Output redacted: {output_verdict.detail}", "response_snippet": response_text[:500] if response_text else "", "matched_patterns": getattr(output_verdict, 'matched_patterns', [])},
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )

        # ── Await deep scan result if running ──
        if deep_scan_task is not None:
            try:
                scan_code, scan_resp = await deep_scan_task
                if scan_code == 200 and isinstance(scan_resp, dict):
                    scan_action = scan_resp.get("recommended_action") or ""
                    if scan_action in ("block_immediately", "block_and_alert"):
                        # Check if the deep scan threats are injection-related
                        _deep_threats = scan_resp.get("threats_detected") or []
                        _deep_injection_types = {"prompt_injection", "jailbreak", "goal_hijacking", "tool_overreach"}
                        _deep_has_injection = any(
                            t.get("type") in _deep_injection_types
                            for t in _deep_threats if isinstance(t, dict)
                        )
                        # Respect scan_block_on_injection for injection-type deep scan blocks
                        if _deep_has_injection and not org_config.get("scan_block_on_injection", True):
                            LOG.info(
                                "Backend deep scan detected injection but scan_block_on_injection=False (user=%s)",
                                user_id,
                            )
                        elif enforcement_mode != "block":
                            LOG.warning(
                                "MONITOR: Backend deep scan would block (action=%s, user=%s)",
                                scan_action, user_id,
                            )
                        else:
                            LOG.warning("Backend deep scan blocked response (action=%s, user=%s)", scan_action, user_id)
                            METRICS["blocked"] += 1
                            elapsed_ms = (time.perf_counter() - start) * 1000
                            return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                                action="block",
                                reason="Response blocked by backend deep security scan.",
                                detection_tier="policy",
                                original_prompt=prompt,
                                processing_time_ms=elapsed_ms,
                                security_incident=True,
                            ))
            except Exception:
                LOG.exception("Backend deep scan failed, continuing with local scan results")

        if response_text and AGENT_ID and org_config.get("output_policy_enabled", True):
            _, resp_check = await asyncio.to_thread(
                _policy_check_cached,
                effective_prompt,
                response_text,
                user_id,
                endpoint_id,
                agent_data,
                project_id,
                risk_score,
                requested_model,
                org_slug,
            )
            resp_action = _normalize_output_action(resp_check.get("action") if resp_check else "allow")
            if resp_check and resp_check.get("event_id"):
                _policy_audit_event_id = resp_check.get("event_id")
            # §1.7 output policy control: the operator-configured output_policy_action
            # is authoritative for the output path. When the policy engine reports a
            # violation, route it through the configured action (block/redact/rewrite/
            # flag/allow). action="allow" lets operators monitor without enforcing.
            # Monitor normalizes to flag — do not escalate flag/monitor to block.
            if resp_check and resp_action not in ("allow", "flag"):
                resp_action = _normalize_output_action(org_config.get("output_policy_action", "block"))
            if resp_check and resp_action == "block":
                METRICS["blocked"] += 1
                elapsed_ms = (time.perf_counter() - start) * 1000
                resp_categories = resp_check.get("matched_policy_categories") or []
                resp_threat_type = _category_to_threat_type(resp_categories[0]) if resp_categories else "policy_violation"
                if TELEMETRY is not None and not resp_check.get("event_id"):
                    _gateway_lifecycle_telemetry_emitted = True
                    _emit_telemetry(
                        status_code=200,
                        event_type="output_scan",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.85,
                        threat_type=resp_threat_type,
                        metadata={
                            "detail": resp_check.get("message") or "Post-LLM policy block",
                            "matched_policies": resp_check.get("matched_policies") or [],
                            "matched_rules": resp_check.get("matched_rules") or [],
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="block",
                    rule_code=((resp_check.get("matched_rules") or ["policy_response_block"])[0]),
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "matched_rules": resp_check.get("matched_rules") or [],
                        "matched_policies": resp_check.get("matched_policies") or [],
                    },
                )
                return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                    action="block",
                    reason=resp_check.get("message") or "Response blocked by post-LLM policy check.",
                    detail=resp_check.get("message") or "Response blocked by post-LLM policy check.",
                    detection_tier="policy",
                    threat_type=resp_threat_type,
                    matched_patterns=resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                    original_prompt=prompt,
                    processing_time_ms=elapsed_ms,
                    security_incident=True,
                ))
            if resp_check and resp_action == "redact" and resp_check.get("redacted_response"):
                redacted_response = resp_check["redacted_response"]
                _set_completion_response_text(llm_resp, redacted_response)
                response_text = redacted_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "redact",
                        "reason": resp_check.get("message") or "Response redacted by post-LLM policy check.",
                        "detail": resp_check.get("message") or "Response redacted by post-LLM policy check.",
                        "detection_tier": "policy",
                        "threat_type": "policy_violation",
                        "confidence": 0.8,
                        "matched_patterns": resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                        "redacted_response": redacted_response,
                    },
                )
            elif resp_check and resp_action == "rewrite":
                resp_categories = resp_check.get("matched_policy_categories") or []
                resp_threat_type = _category_to_threat_type(resp_categories[0]) if resp_categories else "policy_violation"
                rewritten_response = _rewrite_output_response_text(resp_threat_type, resp_check.get("message"))
                _set_completion_response_text(llm_resp, rewritten_response)
                response_text = rewritten_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "rewrite",
                        "reason": resp_check.get("message") or "Response rewritten by post-LLM policy check.",
                        "detail": resp_check.get("message") or "Response rewritten by post-LLM policy check.",
                        "detection_tier": "policy",
                        "threat_type": resp_threat_type,
                        "confidence": 0.65,
                        "matched_patterns": resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                        "rewritten_response": rewritten_response,
                    },
                )
                if TELEMETRY is not None and not resp_check.get("event_id"):
                    _gateway_lifecycle_telemetry_emitted = True
                    _emit_telemetry(
                        status_code=200,
                        event_type="output_scan",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="rewrite",
                        risk_score=0.65,
                        threat_type=resp_threat_type,
                        metadata={
                            "detail": resp_check.get("message") or "Post-LLM policy rewrite",
                            "matched_policies": resp_check.get("matched_policies") or [],
                            "matched_rules": resp_check.get("matched_rules") or [],
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="rewrite",
                    rule_code=((resp_check.get("matched_rules") or ["policy_response_rewrite"])[0]),
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "matched_rules": resp_check.get("matched_rules") or [],
                        "matched_policies": resp_check.get("matched_policies") or [],
                    },
                )
            elif resp_check and resp_action == "flag":
                resp_categories = resp_check.get("matched_policy_categories") or []
                resp_threat_type = _category_to_threat_type(resp_categories[0]) if resp_categories else "policy_violation"
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "flag",
                        "reason": resp_check.get("message") or "Response flagged by post-LLM policy check.",
                        "detail": resp_check.get("message") or "Response flagged by post-LLM policy check.",
                        "detection_tier": "policy",
                        "threat_type": resp_threat_type,
                        "confidence": 0.55,
                        "matched_patterns": resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                        "review_required": True,
                    },
                )
                if TELEMETRY is not None and not resp_check.get("event_id"):
                    _gateway_lifecycle_telemetry_emitted = True
                    _emit_telemetry(
                        status_code=200,
                        event_type="output_scan",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="flag",
                        risk_score=0.55,
                        threat_type=resp_threat_type,
                        metadata={
                            "detail": resp_check.get("message") or "Post-LLM policy flag",
                            "matched_policies": resp_check.get("matched_policies") or [],
                            "matched_rules": resp_check.get("matched_rules") or [],
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )

        # Record token usage for rate limiting and observability
        usage = LLM_ROUTER.extract_usage(llm_resp)
        if usage:
            LOG.info(
                "Token usage: %s (model=%s, user=%s, project=%s)",
                usage, body.get("model"), user_id, project_id,
            )
            if RATE_LIMITER is not None and auth_ctx is not None:
                await RATE_LIMITER.record_usage(
                    auth_ctx.key_hash,
                    usage.get("total_tokens", 0),
                )

        METRICS["allowed"] += 1
        elapsed_ms = (time.perf_counter() - start) * 1000
        _tel_action = "redact" if redacted_prompt is not None else "allow"
        _tel_threat = (scan_verdict.threat_type if scan_verdict and redacted_prompt is not None else "")
        _tel_risk = (scan_verdict.confidence if scan_verdict and redacted_prompt is not None else 0.0)
        telemetry_start = time.perf_counter()
        if _policy_audit_event_id is None and not _gateway_lifecycle_telemetry_emitted:
            _request_metadata: dict = {"stage_metrics_ms": stage_metrics}
            if isinstance(route_metadata, dict) and route_metadata:
                _request_metadata.update(route_metadata)
                if route_selection is not None:
                    _request_metadata["source"] = "routing"
            _emit_telemetry(
                event_type="request",
                model=body.get("model", ""),
                user_id=user_id,
                project_id=str(project_id or ""),
                key_prefix=auth_ctx.prefix if auth_ctx else "",
                prompt_snippet=_prompt_snippet,
                endpoint_id=endpoint_id,
                latency_ms=elapsed_ms,
                risk_score=_tel_risk,
                action=_tel_action,
                threat_type=_tel_threat,
                tokens_used=usage or {},
                metadata=_request_metadata,
            )
        stage_metrics["telemetry_enqueue_ms"] = round((time.perf_counter() - telemetry_start) * 1000, 2)
        if tier2_execution_mode == "async_post_llm":
            await enqueue_job(
                job_type="chat_postprocess",
                request_id=request.headers.get("X-Request-ID", f"zs-post-{_uuid.uuid4().hex[:12]}"),
                org_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                payload={
                    "request_id": request.headers.get("X-Request-ID", ""),
                    "organization_id": getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                    "user_id": user_id,
                    "project_id": str(project_id or ""),
                    "model": body.get("model", ""),
                    "action": _tel_action,
                    "threat_type": _tel_threat,
                    "response_snippet": (response_text or "")[:500],
                },
            )
        response_headers_final: dict[str, str] = {}
        elapsed_ms = (time.perf_counter() - start) * 1000
        if isinstance(llm_resp, dict):
            if output_enforcement is not None:
                llm_resp["zeroshield"] = _build_zeroshield_metadata(
                    action=output_enforcement.get("action", "allow"),
                    reason=output_enforcement.get("reason") or "Output checks passed.",
                    detection_tier=output_enforcement.get("detection_tier", "output_guard"),
                    threat_type=output_enforcement.get("threat_type", "none"),
                    confidence=float(output_enforcement.get("confidence") or 0.0),
                    matched_patterns=output_enforcement.get("matched_patterns") or [],
                    compliance_tags=output_enforcement.get("compliance_tags"),
                    original_prompt=prompt,
                    redacted_prompt=redacted_prompt,
                    redacted_response=output_enforcement.get("redacted_response"),
                    rewritten_response=output_enforcement.get("rewritten_response"),
                    detail=output_enforcement.get("detail"),
                    processing_time_ms=elapsed_ms,
                    review_required=bool(output_enforcement.get("review_required")),
                    security_incident=bool(output_enforcement.get("security_incident")),
                    factuality_warning=bool(output_enforcement.get("factuality_warning")) or hallucination_flagged,
                    routing=route_metadata,
                )
                response_headers_final["X-ZeroShield-Action"] = output_enforcement.get("action", "allow")
                if output_enforcement.get("matched_patterns"):
                    response_headers_final["X-ZeroShield-Matched-Patterns"] = ",".join(output_enforcement.get("matched_patterns") or [])
                if output_enforcement.get("action") == "redact" and output_enforcement.get("redacted_response") is not None:
                    response_headers_final["X-ZeroShield-Redacted-Types"] = ",".join(output_enforcement.get("matched_patterns") or [])
                if output_enforcement.get("review_required"):
                    response_headers_final["X-ZeroShield-Review-Required"] = "true"
            elif redacted_prompt is not None:
                llm_resp["zeroshield"] = _build_zeroshield_metadata(
                    action="redact",
                    reason=f"PII detected in prompt ({', '.join(scan_verdict.matched_patterns if scan_verdict else [])}). Redacted before forwarding to LLM.",
                    detection_tier=scan_verdict.tier if scan_verdict else "tier_1",
                    threat_type=scan_verdict.threat_type if scan_verdict else "pii",
                    confidence=scan_verdict.confidence if scan_verdict else 0.85,
                    matched_patterns=scan_verdict.matched_patterns if scan_verdict else [],
                    original_prompt=prompt,
                    redacted_prompt=redacted_prompt,
                    detail=scan_verdict.detail if scan_verdict else "",
                    processing_time_ms=elapsed_ms,
                    routing=route_metadata,
                )
                response_headers_final["X-ZeroShield-Action"] = "redact"
                response_headers_final["X-ZeroShield-Redacted-Types"] = ",".join(
                    scan_verdict.matched_patterns if scan_verdict else []
                )
            else:
                zs_action, zs_reason, zs_threat, zs_conf, zs_patterns, zs_detail = _resolve_success_metadata_from_verdict(scan_verdict)
                llm_resp["zeroshield"] = _build_zeroshield_metadata(
                    action=zs_action,
                    reason=zs_reason,
                    detection_tier=scan_verdict.tier if scan_verdict else "none",
                    threat_type=zs_threat,
                    confidence=zs_conf,
                    matched_patterns=zs_patterns,
                    original_prompt=prompt,
                    detail=zs_detail,
                    processing_time_ms=elapsed_ms,
                    factuality_warning=hallucination_flagged,
                    routing=route_metadata,
                )
                if zs_action == "flag":
                    response_headers_final["X-ZeroShield-Action"] = "flag"
            if hallucination_flagged:
                response_headers_final["X-ZeroShield-Factuality-Warning"] = "true"
                if isinstance(llm_resp.get("zeroshield"), dict):
                    llm_resp["zeroshield"]["factuality_warning"] = True
            if isinstance(llm_resp.get("zeroshield"), dict) and isinstance(route_metadata, dict):
                llm_resp["zeroshield"]["routing_enabled"] = route_metadata.get("routing_enabled", True)
                llm_resp["zeroshield"]["routing_override"] = route_metadata.get("routing_override")
                llm_resp["zeroshield"]["routing_score"] = route_metadata.get("routing_score")
                llm_resp["zeroshield"]["decision_factors"] = route_metadata.get("decision_factors", [])
                llm_resp["zeroshield"]["weights"] = route_metadata.get("weights", {})
                llm_resp["zeroshield"]["fallback_chain"] = route_metadata.get("fallback_chain", [])
                llm_resp["zeroshield"]["candidate_count"] = route_metadata.get("candidate_count")
                llm_resp["zeroshield"]["data_sensitivity"] = route_metadata.get("data_sensitivity")
                llm_resp["zeroshield"]["compliance_requirements"] = route_metadata.get("compliance_requirements", [])
                llm_resp["zeroshield"]["request_risk_score"] = route_metadata.get("request_risk_score")
                llm_resp["zeroshield"]["estimated_tokens"] = route_metadata.get("estimated_tokens")
                llm_resp["zeroshield"]["token_budget_tpm"] = route_metadata.get("token_budget_tpm")
                llm_resp["zeroshield"]["latency_budget_ms"] = route_metadata.get("latency_budget_ms")
            if route_selection is not None:
                response_headers_final["X-ZeroShield-Original-Model"] = route_selection.requested_model or "auto"
                response_headers_final["X-ZeroShield-Routed-Model"] = route_selection.model_name
                response_headers_final["X-ZeroShield-Routing-Source"] = route_selection.decision_source
                response_headers_final["X-ZeroShield-Rerouted"] = "true" if route_selection.requested_model and route_selection.requested_model != "auto" and route_selection.model_name != route_selection.requested_model else "false"
                response_headers_final["X-ZeroShield-Routing-Reason"] = route_selection.reason[:180]
                if route_selection.policy_summary and bool(
                    org_config.get(
                        "stream_emit_debug_headers",
                        CONFIG.get("stream_emit_debug_headers", False),
                    )
                ):
                    response_headers_final["X-ZeroShield-Routing-Policy-Summary"] = (
                        route_selection.policy_summary[:180]
                    )
                if isinstance(llm_resp.get("zeroshield"), dict):
                    llm_resp["zeroshield"]["selected_model"] = route_selection.model_name
                    llm_resp["zeroshield"]["original_model"] = route_selection.requested_model or "auto"
                    llm_resp["zeroshield"]["rerouted"] = bool(route_selection.requested_model and route_selection.requested_model != "auto" and route_selection.model_name != route_selection.requested_model)
                    llm_resp["zeroshield"]["routing_reason"] = route_selection.reason
                    llm_resp["zeroshield"]["decision_source"] = route_selection.decision_source
                    llm_resp["zeroshield"]["policy_summary"] = route_selection.policy_summary
        
        # Operator pipeline trace (Module 1.1 simulator) — built before zeroshield redaction.
        if isinstance(llm_resp, dict):
            from pipeline_trace import build_pipeline_trace

            _zs_full = llm_resp.get("zeroshield") if isinstance(llm_resp.get("zeroshield"), dict) else {}
            _final = _zs_full.get("action") or "allow"
            llm_resp["pipeline_trace"] = build_pipeline_trace(
                prompt=prompt,
                stage_metrics=stage_metrics,
                final_action=_final,
                blocked_stage="",
                http_status=200,
                scan_verdict=scan_verdict,
                route_metadata=route_metadata,
                zeroshield=_zs_full,
                response_text=response_text or "",
                requested_model=body.get("model", ""),
                output_scan_verdict=output_verdict,
            )

        # ── SECURITY FIX: Redact sensitive fields from zeroshield metadata before returning to client ──
        # The zeroshield object contains internal security details that MUST NOT be exposed to clients.
        if isinstance(llm_resp.get("zeroshield"), dict):
            llm_resp["zeroshield"] = _redact_for_client_response(llm_resp["zeroshield"]) or {}

        return JSONResponse(content=llm_resp, headers=response_headers_final)

    except Exception as exc:
        LOG.exception("Unhandled error in proxy_chat")
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An internal gateway error occurred.",
                "code": "gateway_internal_error",
            },
        )

    finally:
        METRICS["active_connections"] -= 1
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000


@app.post(
    "/v1/embeddings",
    summary="Create embeddings (OpenAI-compatible proxy)",
    description=(
        "Proxy endpoint for OpenAI-compatible embedding creation.\n\n"
        "**Flow:**\n"
        "1. Authenticates the request via Gateway API Key\n"
        "2. Checks the model against the key's allowed_models list\n"
        "3. Forwards to the embedding provider via LiteLLM\n"
        "4. Returns the embedding response\n\n"
        "**Headers:**\n"
        "- `Authorization: Bearer <gateway_api_key>` (required)"
    ),
    response_description="OpenAI-compatible embedding response",
    tags=["Embeddings"],
    responses={
        200: {
            "description": "Successful embedding creation",
            "content": {
                "application/json": {
                    "example": {
                        "object": "list",
                        "data": [
                            {
                                "object": "embedding",
                                "embedding": [0.0023064255, -0.009327292],
                                "index": 0,
                            }
                        ],
                        "model": "text-embedding-3-small",
                        "usage": {
                            "prompt_tokens": 8,
                            "total_tokens": 8,
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid JSON body or missing input"},
        403: {"description": "Model not in allowlist"},
        502: {"description": "Upstream embedding request failed"},
    },
)
async def proxy_embeddings(request: Request):
    """Proxy to upstream embedding model. OpenAI-compatible endpoint."""
    METRICS["total_requests"] += 1
    start = time.perf_counter()

    try:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        if not body.get("input"):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request_error",
                    "message": "`input` is required.",
                },
            )

        auth_ctx = getattr(request.state, "auth_context", None)
        org_slug = auth_ctx.org_slug if auth_ctx else ""
        org_config = CONFIG_SYNC.get_config(org_slug) if CONFIG_SYNC else CONFIG

        if auth_ctx is not None:
            user_id = auth_ctx.user_id
            project_id = auth_ctx.project_id
            allowed_models = auth_ctx.allowed_models

            requested_model = body.get("model", "text-embedding-3-small")
            if allowed_models and requested_model not in allowed_models:
                METRICS["blocked"] += 1
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Model '{requested_model}' is not in your allowlist.",
                        "code": "model_not_allowed",
                    },
                )
        else:
            user_id = None
            project_id = None

        if LLM_ROUTER is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "message": "LLM router not initialized.",
                },
            )

        # ── Phase 1 §1.1: per-org TPM ceiling (fail-CLOSED) ──
        # Embeddings can be a high-volume vector for resource exhaustion across
        # tenants; enforce the same ceiling as /v1/chat. Estimate token cost from
        # the `input` payload (string or list of strings).
        _emb_input = body.get("input") or ""
        if isinstance(_emb_input, list):
            _emb_text = " ".join(str(x) for x in _emb_input if x)
        else:
            _emb_text = str(_emb_input)
        _emb_est_tokens = _estimate_request_tokens(_emb_text)
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="embedding_blocked",
            model=body.get("model", "text-embedding-3-small"),
            user_id=user_id,
            project_id=str(project_id or ""),
            estimated_tokens=_emb_est_tokens,
        )
        if _rl_resp is not None:
            return _rl_resp

        status, result = await LLM_ROUTER.aembedding(body)

        elapsed_ms = (time.perf_counter() - start) * 1000
        response_headers = {
            "X-ZeroShield-Action": "pass",
            "X-Processing-Time-Ms": f"{elapsed_ms:.1f}",
        }

        _emit_telemetry(
            status_code=status,
            event_type="embedding_request",
            model=body.get("model", "text-embedding-3-small"),
            user_id=user_id,
            project_id=str(project_id or ""),
            key_prefix=auth_ctx.prefix if auth_ctx else "",
            organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
            action="pass",
            risk_score=0.0,
            metadata={
                "status": status,
                "processing_time_ms": elapsed_ms,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(status_code=status, content=result, headers=response_headers)
    finally:
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000


# ── Dynamic vector client resolution (org config → env-var default) ──

def _resolve_vector_client(vector_db_type: str, org_id: int | str | None = None):
    """
    Resolve a vector DB client using the credential hierarchy:
        1. Org-level VectorProviderConfig from VECTOR_PROVIDER_SYNC cache
        2. Gateway-level VECTOR_CLIENTS from env-var startup
    Returns (client, provider_type_used) or (None, None).
    """
    # Try org-level config first
    if org_id and VECTOR_PROVIDER_SYNC is not None:
        cfg = VECTOR_PROVIDER_SYNC.get_provider_config(org_id, vector_db_type)
        if cfg and cfg.get("is_active"):
            from vector_client import PineconeClient, MilvusClient
            try:
                if vector_db_type == "pinecone" and cfg.get("api_key"):
                    return PineconeClient(
                        api_key=cfg["api_key"],
                        environment=cfg.get("environment", ""),
                        embedding_model=cfg.get("embedding_model", "text-embedding-3-small"),
                    ), "pinecone"
                elif vector_db_type == "milvus" and cfg.get("connection_url"):
                    return MilvusClient(
                        uri=cfg["connection_url"],
                        token=cfg.get("api_key", ""),
                    ), "milvus"
                elif vector_db_type == "custom" and cfg.get("connection_url"):
                    # Custom provider support is currently backed by Milvus-compatible
                    # URI/token contracts until dedicated adapters are introduced.
                    return MilvusClient(
                        uri=cfg["connection_url"],
                        token=cfg.get("api_key", ""),
                    ), "custom"
            except Exception:
                LOG.warning("Failed to create org-level %s client for org=%s, falling back to default", vector_db_type, org_id)

    # Fall back to gateway-level default
    client = VECTOR_CLIENTS.get(vector_db_type)
    if client:
        return client, vector_db_type

    # If requested type not found, try any available
    if VECTOR_CLIENTS:
        fallback_type = next(iter(VECTOR_CLIENTS))
        return VECTOR_CLIENTS[fallback_type], fallback_type

    return None, None


def _is_valid_collection_name(name: str) -> bool:
    """Validate user-facing collection identifiers.

    Reject tenant prefix separators to prevent cross-tenant namespace injection
    attempts like "other_tenant__collection".
    """
    if not name or "__" in name:
        return False
    if len(name) > 128:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", name))


@app.post(
    "/v1/rag/query",
    summary="RAG vector query (with firewall enforcement)",
    description=(
        "Gateway-proxied vector database query for RAG workloads.\n\n"
        "**Flow:**\n"
        "1. Authenticates the request via Gateway API Key\n"
        "2. Looks up VectorCollectionPolicy for the requested collection\n"
        "3. Enforces collection-level RBAC (allowed operations, max results, query length)\n"
        "4. Scans the query for prompt injection / vector injection attacks\n"
        "5. Executes the query against the configured vector database\n"
        "6. Scans retrieved documents for indirect injection, PII, secrets, toxicity\n"
        "7. Detects embedding anomalies via distance threshold + statistical outlier analysis\n"
        "8. Returns filtered results with scan verdict metadata\n\n"
        "**Headers:**\n"
        "- `Authorization: Bearer <gateway_api_key>` (required)\n\n"
        "**Namespace Isolation:**\n"
        "The actual vector DB collection is namespaced as `{project_id}__{collection_name}` "
        "to prevent cross-tenant data leakage."
    ),
    tags=["RAG"],
    responses={
        200: {
            "description": "Successful RAG query with filtered results",
            "content": {
                "application/json": {
                    "example": {
                        "collection": "customer_docs",
                        "query": "What is the refund policy?",
                        "documents": [
                            {
                                "id": "doc-123",
                                "content": "Our refund policy states...",
                                "metadata": {"category": "policies"},
                                "distance": 0.12,
                            }
                        ],
                        "total_retrieved": 6,
                        "filtered_count": 1,
                        "scan_verdict": {
                            "action": "allow",
                            "flagged_documents": [],
                            "detail": "",
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid request body or missing required fields"},
        403: {
            "description": "Blocked by vector policy, injection detected, or access denied",
            "content": {
                "application/json": {
                    "examples": {
                        "no_policy": {
                            "value": {
                                "error": "forbidden",
                                "message": "Access denied: no policy for collection 'customer_docs'.",
                                "code": "rag_access_denied",
                            }
                        },
                        "injection_blocked": {
                            "value": {
                                "error": "blocked",
                                "message": "RAG query blocked: prompt_injection detected.",
                                "code": "rag_query_blocked",
                                "threat_type": "prompt_injection",
                            }
                        },
                    }
                }
            },
        },
        503: {"description": "Vector DB client not configured or RAG firewall not enabled"},
    },
)
async def rag_query(request: Request):
    """Proxy to vector database after pipeline-aware policy enforcement."""
    METRICS["total_requests"] += 1
    METRICS["active_connections"] += 1
    start_rag = time.perf_counter()

    try:
        if RAG_PIPELINE is None or VECTOR_POLICY_SYNC is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "message": "RAG firewall not enabled. Set GATEWAY_RAG_ENABLED=true.",
                    "code": "rag_not_enabled",
                },
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        collection_name = body.get("collection", "").strip()
        query_text = body.get("query", "").strip()
        n_results = body.get("n_results", CONFIG.get("rag_default_max_results", 10))
        vector_db_type = body.get("vector_db_type", "pinecone").strip()
        where_filter = body.get("where")
        namespace = body.get("namespace", "")

        if not collection_name:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "Missing required field: 'collection'."},
            )
        if not _is_valid_collection_name(collection_name):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                    "code": "rag_namespace_violation",
                },
            )
        if not query_text:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "Missing required field: 'query'."},
            )

        auth_ctx = getattr(request.state, "auth_context", None)
        if auth_ctx is None:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Authentication required.",
                    "code": "auth_required",
                },
            )
        project_id = auth_ctx.project_id

        # ── Phase 1 §1.1: per-org TPM ceiling on RAG query traffic ──
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="rag_query_blocked",
            model=body.get("embedding_model", "") or "",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=str(project_id or ""),
            estimated_tokens=_estimate_request_tokens(query_text),
        )
        if _rl_resp is not None:
            return _rl_resp

        policy = VECTOR_POLICY_SYNC.get_policy(str(project_id), collection_name)
        if policy is None:
            # Fallback: generate a permissive default policy for unknown collections
            # so the pipeline still runs (with monitoring). Admins can create explicit
            # deny policies later via the Vector Policy UI.
            LOG.info(
                "No explicit vector policy for %s/%s — applying default monitor policy (user=%s)",
                project_id, collection_name, auth_ctx.user_id,
            )
            policy = {
                "enabled": True,
                "collection_name": collection_name,
                "project_id": str(project_id),
                "default_action": "monitor",
                "allowed_operations": ["query", "insert"],
                "max_results_per_query": 50,
                "max_query_length": 2048,
                "require_context_scan": True,
                "block_sensitive_documents": False,
                "sensitive_fields": [],
                "anomaly_distance_threshold": None,
                "embedding_model": "",
                "embedding_dimension": None,
            }

        if not policy.get("enabled", False):
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Policy for collection '{collection_name}' is disabled.",
                    "code": "rag_policy_disabled",
                },
            )

        allowed_ops = policy.get("allowed_operations", [])
        if "query" not in allowed_ops:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Query operation not permitted on this collection.",
                    "code": "rag_operation_denied",
                },
            )

        # ── Embedding access control enforcement ──
        req_embedding_model = body.get("embedding_model", "")
        req_embedding_dim = body.get("embedding_dimension")
        policy_embedding_model = policy.get("embedding_model", "")
        policy_embedding_dim = policy.get("embedding_dimension")

        if policy_embedding_model and req_embedding_model and req_embedding_model != policy_embedding_model:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Embedding model mismatch: expected '{policy_embedding_model}', got '{req_embedding_model}'.",
                    "code": "embedding_model_mismatch",
                },
            )
        if policy_embedding_dim and req_embedding_dim and int(req_embedding_dim) != int(policy_embedding_dim):
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Embedding dimension mismatch: expected {policy_embedding_dim}, got {req_embedding_dim}.",
                    "code": "embedding_dimension_mismatch",
                },
            )

        max_results = policy.get("max_results_per_query", CONFIG.get("rag_default_max_results", 10))
        n_results = min(n_results, max_results)

        # ── Execute 4-stage RAG Firewall Pipeline ──
        result = await RAG_PIPELINE.execute(
            query_text=query_text,
            collection_name=collection_name,
            project_id=str(project_id),
            vector_db_type=policy.get("vector_db_type", vector_db_type),
            n_results=n_results,
            where_filter=where_filter,
            namespace=namespace,
            policy=policy,
            key_hash=getattr(auth_ctx, "key_hash", ""),
            organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
            user_id=getattr(auth_ctx, "user_id", None) if auth_ctx else None,
        )

        if result.action == "block":
            METRICS["blocked"] += 1
            last = result.pipeline_context.stages[-1] if result.pipeline_context and result.pipeline_context.stages else None
            elapsed_rag_ms = (time.perf_counter() - start_rag) * 1000
            _emit_telemetry(
                status_code=403,
                event_type="rag_query",
                model="",
                user_id=getattr(auth_ctx, "user_id", ""),
                project_id=str(project_id),
                key_prefix=getattr(auth_ctx, "prefix", ""),
                organization_id=getattr(auth_ctx, "organization_id", None),
                action="block",
                risk_score=last.verdict.confidence if last else 0.0,
                threat_type=last.verdict.threat_type if last else "",
                pipeline_stage=last.stage_name if last else "unknown",
                latency_ms=elapsed_rag_ms,
                metadata={
                    "collection": collection_name,
                    "vector_db_type": vector_db_type,
                    "request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                    "pipeline_request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                    "blocked_at_stage": last.stage_name if last else "unknown",
                    "detail": last.verdict.detail if last else "",
                    "module": "1.3",
                    "module_id": "1.3",
                },
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "blocked",
                    "message": f"RAG pipeline blocked at {last.stage_name if last else 'unknown'}: {last.verdict.detail if last else ''}",
                    "code": "rag_pipeline_blocked",
                    "threat_type": last.verdict.threat_type if last else "",
                    "pipeline_stage": last.stage_name if last else "",
                },
            )

        METRICS["allowed"] += 1
        elapsed_rag_ms = (time.perf_counter() - start_rag) * 1000
        LOG.info(
            "RAG query completed (project=%s, collection=%s, retrieved=%d, filtered=%d, stages=%d)",
            project_id, collection_name, result.total_retrieved, result.filtered_count,
            len(result.pipeline_context.stages) if result.pipeline_context else 0,
        )

        # ── Top-level RAG query telemetry (feeds general dashboards / threat feed) ──
        _emit_telemetry(
            status_code=200,
            event_type="rag_query",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=str(project_id),
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action=result.action,
            risk_score=0.0,
            pipeline_stage="rag",
            latency_ms=elapsed_rag_ms,
            metadata={
                "collection": collection_name,
                "vector_db_type": vector_db_type,
                "total_retrieved": result.total_retrieved,
                "filtered_count": result.filtered_count,
                "context_binding_id": result.context_binding_id or "",
                "request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                "pipeline_request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                "stages_executed": len(result.pipeline_context.stages) if result.pipeline_context else 0,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        response_content = {
            "collection": collection_name,
            "query": query_text,
            "documents": result.documents,
            "total_retrieved": result.total_retrieved,
            "filtered_count": result.filtered_count,
            "scan_verdict": result.scan_verdict,
            "pipeline_audit": result.pipeline_audit,
            "context_binding_id": result.context_binding_id or "",
            "canary_word": result.canary_word or "",
            "model_downgrade": result.model_downgrade or "",
        }

        headers = {}
        if result.context_binding_id:
            headers["X-ZeroShield-RAG-Context-ID"] = result.context_binding_id
        if result.pipeline_context:
            headers["X-ZeroShield-Pipeline-Request-ID"] = result.pipeline_context.request_id

        return JSONResponse(content=response_content, headers=headers)

    finally:
        METRICS["active_connections"] -= 1
        METRICS["sum_latency_ms"] += (time.perf_counter() - start_rag) * 1000


# ---------------------------------------------------------------------------
# RAG Ingestion, Document Management & Collection Management
# ---------------------------------------------------------------------------

@app.post(
    "/v1/rag/ingest",
    summary="Ingest documents into a vector DB collection (with firewall enforcement)",
    tags=["RAG"],
)
async def rag_ingest(request: Request):
    """
    Production document ingestion with full policy enforcement.

    Flow:
    1. Authenticate via Gateway API Key
    2. Look up VectorCollectionPolicy for the target collection
    3. Verify 'insert' operation is allowed
    4. Scan document content via ContextGuard (injection, PII, toxicity)
    5. Generate embeddings and upsert into the vector database
    6. Return ingestion receipt with scan verdict
    """
    METRICS["total_requests"] += 1
    METRICS["active_connections"] += 1
    start = time.perf_counter()

    try:
        if VECTOR_POLICY_SYNC is None or not VECTOR_CLIENTS:
            return JSONResponse(
                status_code=503,
                content={"error": "service_unavailable", "message": "RAG firewall not enabled."},
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        collection_name = body.get("collection", "").strip()
        documents = body.get("documents", [])
        ids = body.get("ids", [])
        metadatas = body.get("metadatas", [])
        vector_db_type = body.get("vector_db_type", "pinecone").strip()

        # Also accept single-document shorthand
        if not documents and body.get("content"):
            documents = [body["content"]]
            ids = [body.get("id", f"doc-{_uuid.uuid4().hex[:8]}")]
            metadatas = [body.get("metadata", {})]

        if not collection_name:
            return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
        if not _is_valid_collection_name(collection_name):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                    "code": "rag_namespace_violation",
                },
            )
        if not documents:
            return JSONResponse(status_code=400, content={"error": "Missing 'documents' or 'content'."})

        # Auto-generate IDs if not provided
        if not ids or len(ids) != len(documents):
            ids = [f"doc-{_uuid.uuid4().hex[:8]}" for _ in documents]
        if not metadatas or len(metadatas) != len(documents):
            metadatas = [{"source": "gateway"} for _ in documents]
        # Vector providers expect metadata dictionaries for each document.
        metadatas = [m if m else {"source": "gateway"} for m in metadatas]

        # ── Auth ──
        auth_ctx = getattr(request.state, "auth_context", None)
        if auth_ctx is None:
            return JSONResponse(status_code=403, content={"error": "Authentication required."})
        project_id = str(auth_ctx.project_id)

        # ── Phase 1 §1.1: per-org TPM ceiling on RAG ingest (largest write surface) ──
        _ingest_text = " ".join(str(d) for d in documents if d)
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="rag_ingest_blocked",
            model="",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=project_id,
            estimated_tokens=_estimate_request_tokens(_ingest_text),
        )
        if _rl_resp is not None:
            return _rl_resp

        # ── Policy enforcement ──
        policy = VECTOR_POLICY_SYNC.get_policy(project_id, collection_name)
        if policy is None:
            policy = {
                "enabled": True, "collection_name": collection_name,
                "project_id": project_id, "default_action": "monitor",
                "allowed_operations": ["query", "insert"],
                "max_results_per_query": 50, "require_context_scan": True,
            }

        if not policy.get("enabled", False):
            METRICS["blocked"] += 1
            return JSONResponse(status_code=403, content={"error": "Policy disabled for this collection."})

        allowed_ops = policy.get("allowed_operations", [])
        if "insert" not in allowed_ops:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "message": "Insert operation not permitted.", "code": "rag_operation_denied"},
            )

        if _async_vector_ingest_enabled:
            job_id = f"rag-ingest-{_uuid.uuid4().hex[:12]}"
            await enqueue_job(
                job_type="vector_ingest",
                request_id=job_id,
                org_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                payload={
                    "job_id": job_id,
                    "organization_id": getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                    "project_id": project_id,
                    "collection": collection_name,
                    "vector_db_type": vector_db_type,
                    "documents": documents,
                    "ids": ids,
                    "metadatas": metadatas,
                },
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "accepted",
                    "job_id": job_id,
                    "collection": collection_name,
                    "queued": True,
                },
            )

        # ── Content scanning via ContextGuard ──
        scan_results = []
        blocked_indices = []
        if CONTEXT_GUARD is not None and policy.get("require_context_scan", True):
            for i, doc_text in enumerate(documents):
                # Extract text content from doc (may be str or dict)
                if isinstance(doc_text, dict):
                    text_to_scan = doc_text.get("content", "") or doc_text.get("text", "") or str(doc_text)
                else:
                    text_to_scan = str(doc_text)
                verdict = await CONTEXT_GUARD.scan_single_document(text_to_scan)
                scan_results.append({
                    "index": i, "action": verdict.action,
                    "threats": [verdict.threat_type] if verdict.threat_type else [],
                })
                if verdict.action == "block":
                    blocked_indices.append(i)

        if blocked_indices:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "blocked",
                    "message": f"Content blocked by ContextGuard at indices: {blocked_indices}",
                    "code": "rag_content_blocked",
                    "scan_results": scan_results,
                },
            )

        # ── Ingest into vector DB ──
        client = VECTOR_CLIENTS.get(vector_db_type)
        if client is None:
            # Fallback: try first available client
            vector_db_type = next(iter(VECTOR_CLIENTS), "")
            client = VECTOR_CLIENTS.get(vector_db_type)
        if client is None:
            return JSONResponse(status_code=503, content={"error": "No vector clients configured."})

        # Normalize documents to list[str] for vector client
        doc_strings = []
        normalized_ids = []
        normalized_metas = []
        for i, d in enumerate(documents):
            if isinstance(d, dict):
                text = d.get("content", "") or d.get("text", "") or str(d)
                doc_id = d.get("id", ids[i] if i < len(ids) else f"doc-{_uuid.uuid4().hex[:8]}")
                meta = d.get("metadata", metadatas[i] if i < len(metadatas) else {"source": "gateway"})
            else:
                text = str(d)
                doc_id = ids[i] if i < len(ids) else f"doc-{_uuid.uuid4().hex[:8]}"
                meta = metadatas[i] if i < len(metadatas) else {"source": "gateway"}
            doc_strings.append(text)
            normalized_ids.append(doc_id)
            normalized_metas.append(meta if meta else {"source": "gateway"})

        try:
            count = await client.add(
                collection_name=collection_name,
                documents=doc_strings,
                ids=normalized_ids,
                metadatas=normalized_metas,
                project_id=project_id,
            )
        except Exception as exc:
            LOG.exception("RAG ingest failed (provider=%s, collection=%s)", vector_db_type, collection_name)
            return JSONResponse(
                status_code=500,
                content={"error": "ingest_failed", "message": str(exc)},
            )

        METRICS["allowed"] += 1
        elapsed_ms = (time.perf_counter() - start) * 1000
        LOG.info(
            "RAG ingest completed (project=%s, collection=%s, provider=%s, docs=%d, %.1fms)",
            project_id, collection_name, vector_db_type, count, elapsed_ms,
        )

        _emit_telemetry(
            status_code=200,
            event_type="rag_ingest",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=project_id,
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action="allow",
            risk_score=0.0,
            metadata={
                "collection": collection_name,
                "provider": vector_db_type,
                "doc_count": count,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(content={
            "status": "ingested",
            "collection": collection_name,
            "provider": vector_db_type,
            "doc_count": count,
            "ids": ids,
            "scan_results": scan_results,
            "processing_time_ms": round(elapsed_ms, 1),
        })
    finally:
        METRICS["active_connections"] -= 1
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000


@app.delete(
    "/v1/rag/documents",
    summary="Delete documents from a vector DB collection",
    tags=["RAG"],
)
async def rag_delete_documents(request: Request):
    """Delete specific documents by ID from a collection with policy enforcement."""
    METRICS["total_requests"] += 1
    start = time.perf_counter()

    try:
        if VECTOR_POLICY_SYNC is None or not VECTOR_CLIENTS:
            return JSONResponse(status_code=503, content={"error": "RAG firewall not enabled."})

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        collection_name = body.get("collection", "").strip()
        doc_ids = body.get("ids", [])
        vector_db_type = body.get("vector_db_type", "pinecone").strip()

        if not collection_name:
            return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
        if not _is_valid_collection_name(collection_name):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                    "code": "rag_namespace_violation",
                },
            )
        if not doc_ids:
            return JSONResponse(status_code=400, content={"error": "Missing 'ids'."})

        auth_ctx = getattr(request.state, "auth_context", None)
        if auth_ctx is None:
            return JSONResponse(status_code=403, content={"error": "Authentication required."})
        project_id = str(auth_ctx.project_id)

        # ── Phase 1 §1.1: per-org TPM ceiling on RAG delete (abuse vector) ──
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="rag_delete_blocked",
            model="",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=project_id,
            estimated_tokens=max(20, len(doc_ids) * 4),
        )
        if _rl_resp is not None:
            return _rl_resp

        policy = VECTOR_POLICY_SYNC.get_policy(project_id, collection_name)
        if policy and "delete" not in policy.get("allowed_operations", []):
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "message": "Delete operation not permitted.", "code": "rag_operation_denied"},
            )

        client = VECTOR_CLIENTS.get(vector_db_type) or next(iter(VECTOR_CLIENTS.values()), None)
        if client is None:
            return JSONResponse(status_code=503, content={"error": "No vector clients configured."})

        deleted = await client.delete(collection_name=collection_name, ids=doc_ids, project_id=project_id)

        elapsed_ms = (time.perf_counter() - start) * 1000
        LOG.info("RAG delete completed (project=%s, collection=%s, deleted=%d)", project_id, collection_name, deleted)

        _emit_telemetry(
            status_code=200,
            event_type="rag_delete",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=project_id,
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action="allow",
            risk_score=0.0,
            metadata={
                "collection": collection_name,
                "deleted_count": deleted,
                "ids": doc_ids,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(content={
            "status": "deleted", "collection": collection_name,
            "deleted_count": deleted, "ids": doc_ids,
            "processing_time_ms": round(elapsed_ms, 1),
        })
    finally:
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000


@app.get(
    "/v1/rag/collections",
    summary="List vector DB collections for the current project",
    tags=["RAG"],
)
async def rag_list_collections(request: Request):
    """List all collections visible to the authenticated project."""
    if not VECTOR_CLIENTS:
        return JSONResponse(status_code=503, content={"error": "No vector clients configured."})

    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=403, content={"error": "Authentication required."})
    project_id = str(auth_ctx.project_id)

    rl_block = await _enforce_org_tpm_rate_limit(
        auth_ctx,
        event_type="rag_collections_list_blocked",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=project_id,
        estimated_tokens=20,
    )
    if rl_block is not None:
        return rl_block

    namespaced_prefix = f"{project_id}__"

    result: dict[str, list[str]] = {}
    for provider_name, client in VECTOR_CLIENTS.items():
        try:
            if hasattr(client, "list_collections"):
                collections = await client.list_collections(project_id=project_id)
                normalized: list[str] = []
                for name in collections or []:
                    if isinstance(name, str) and name.startswith(namespaced_prefix):
                        normalized.append(name[len(namespaced_prefix):])
                    else:
                        normalized.append(name)
                result[provider_name] = normalized
            else:
                result[provider_name] = []
        except Exception as exc:
            LOG.warning("Failed to list collections for %s: %s", provider_name, exc)
            result[provider_name] = []

    return JSONResponse(content={"project_id": project_id, "collections": result})


@app.post(
    "/v1/rag/collections",
    summary="Create a new vector DB collection",
    tags=["RAG"],
)
async def rag_create_collection(request: Request):
    """Create a new namespaced collection in the specified vector DB."""
    if not VECTOR_CLIENTS:
        return JSONResponse(status_code=503, content={"error": "No vector clients configured."})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    collection_name = body.get("collection", "").strip()
    vector_db_type = body.get("vector_db_type", "pinecone").strip()

    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                "code": "rag_namespace_violation",
            },
        )

    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=403, content={"error": "Authentication required."})
    project_id = str(auth_ctx.project_id)
    org_id = getattr(auth_ctx, "organization_id", None)

    rl_block = await _enforce_org_tpm_rate_limit(
        auth_ctx,
        event_type="rag_collection_create_blocked",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=project_id,
        estimated_tokens=20,
    )
    if rl_block is not None:
        return rl_block

    client, _ = _resolve_vector_client(vector_db_type, org_id=org_id)
    if client is None:
        return JSONResponse(status_code=400, content={"error": f"Unknown provider: {vector_db_type}"})

    try:
        if hasattr(client, "create_collection"):
            namespaced = await client.create_collection(collection_name=collection_name, project_id=project_id)
        else:
            namespaced = f"{project_id}__{collection_name}"

        _emit_telemetry(
            status_code=200,
            event_type="rag_collection_create",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=project_id,
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action="allow",
            risk_score=0.0,
            metadata={
                "collection": collection_name,
                "provider": vector_db_type,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(content={
            "status": "created", "collection": collection_name,
            "namespaced_name": namespaced, "provider": vector_db_type,
        })
    except Exception as exc:
        LOG.exception("Failed to create collection %s", collection_name)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.delete(
    "/v1/rag/collections",
    summary="Delete a vector DB collection",
    tags=["RAG"],
)
async def rag_delete_collection(request: Request):
    """Delete a namespaced collection from the specified vector DB."""
    if not VECTOR_CLIENTS:
        return JSONResponse(status_code=503, content={"error": "No vector clients configured."})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    collection_name = body.get("collection", "").strip()
    vector_db_type = body.get("vector_db_type", "pinecone").strip()

    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                "code": "rag_namespace_violation",
            },
        )

    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=403, content={"error": "Authentication required."})
    project_id = str(auth_ctx.project_id)

    rl_block = await _enforce_org_tpm_rate_limit(
        auth_ctx,
        event_type="rag_collection_delete_blocked",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=project_id,
        estimated_tokens=20,
    )
    if rl_block is not None:
        return rl_block

    # Check policy allows delete
    policy = VECTOR_POLICY_SYNC.get_policy(project_id, collection_name) if VECTOR_POLICY_SYNC else None
    if policy and "delete" not in policy.get("allowed_operations", []):
        METRICS["blocked"] += 1
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "Delete not permitted by policy.", "code": "rag_operation_denied"},
        )

    client = VECTOR_CLIENTS.get(vector_db_type)
    if client is None:
        client, _ = _resolve_vector_client(vector_db_type, org_id=getattr(auth_ctx, "organization_id", None))
    if client is None:
        return JSONResponse(status_code=400, content={"error": f"Unknown provider: {vector_db_type}"})

    try:
        if hasattr(client, "delete_collection"):
            ok = await client.delete_collection(collection_name=collection_name, project_id=project_id)
        else:
            ok = False
        if ok:
            _emit_telemetry(
                status_code=200,
                event_type="rag_collection_delete",
                model="",
                user_id=getattr(auth_ctx, "user_id", ""),
                project_id=project_id,
                key_prefix=getattr(auth_ctx, "prefix", ""),
                organization_id=getattr(auth_ctx, "organization_id", None),
                action="allow",
                risk_score=0.0,
                metadata={
                    "collection": collection_name,
                    "provider": vector_db_type,
                    "module": "1.3",
                    "module_id": "1.3",
                },
            )
            return JSONResponse(content={"status": "deleted", "collection": collection_name, "provider": vector_db_type})
        else:
            return JSONResponse(status_code=404, content={"error": f"Collection '{collection_name}' not found."})
    except Exception as exc:
        LOG.exception("Failed to delete collection %s", collection_name)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/v1/admin/db-test",
    summary="Test vector database connection",
    tags=["Admin"],
)
async def admin_db_test(request: Request):
    """Test connectivity to a vector database (Pinecone, Milvus, or custom Milvus-compatible URI)."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    body = await request.json()
    provider = body.get("provider", "").lower()
    connection_url = body.get("connection_url", "")
    api_key = body.get("api_key", "")
    environment = body.get("environment", "")

    import time as _time
    start = _time.perf_counter()

    try:
        if provider == "pinecone":
            from pinecone import Pinecone
            pc = Pinecone(api_key=api_key)
            indexes = pc.list_indexes()
            index_list = []
            if hasattr(indexes, 'indexes') and indexes.indexes:
                index_list = [idx.name if hasattr(idx, 'name') else str(idx) for idx in indexes.indexes]
            elif hasattr(indexes, '__iter__'):
                index_list = [idx.name if hasattr(idx, 'name') else str(idx) for idx in indexes]
            elapsed = round((_time.perf_counter() - start) * 1000)
            return JSONResponse(content={
                "status": "connected",
                "provider": "pinecone",
                "details": f"Indexes: {len(index_list)}",
                "indexes": index_list,
                "latency_ms": elapsed,
            })

        elif provider in {"milvus", "custom"}:
            from pymilvus import connections, utility
            from urllib.parse import urlparse
            parsed = urlparse(connection_url or "http://localhost:19530")
            connections.connect(
                alias="test_conn",
                host=parsed.hostname or "localhost",
                port=str(parsed.port or 19530),
                token=api_key or "",
            )
            colls = utility.list_collections(using="test_conn")
            connections.disconnect("test_conn")
            elapsed = round((_time.perf_counter() - start) * 1000)
            return JSONResponse(content={
                "status": "connected",
                "provider": provider,
                "details": f"Collections: {len(colls)}",
                "collections": list(colls) if colls else [],
                "latency_ms": elapsed,
            })

        else:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": f"Unsupported provider: {provider}"},
            )
    except Exception as exc:
        elapsed = round((_time.perf_counter() - start) * 1000)
        LOG.warning("DB connection test failed for %s: %s", provider, exc)
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "provider": provider,
                "error": str(exc),
                "latency_ms": elapsed,
            },
        )


@app.post(
    "/v1/admin/bedrock-test",
    summary="Test Bedrock API connectivity",
    tags=["Admin"],
)
async def admin_bedrock_test(request: Request):
    """Test AWS Bedrock API connectivity and optionally scan a test prompt."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    body = await request.json()
    check_health = body.get("check_health", False)
    prompt = body.get("prompt", "")

    import time as _time
    result = {}

    if check_health:
        try:
            from bedrock_client import BedrockClient
            import os
            client = BedrockClient()
            start = _time.perf_counter()
            available = client.is_available()
            elapsed = round((_time.perf_counter() - start) * 1000)
            result["health"] = {
                "available": available,
                "region": client.region,
                "model": client.model_id,
                "latency_ms": elapsed,
            }
            result["status"] = "ok" if available else "unavailable"
        except Exception as exc:
            LOG.warning("Bedrock health check failed: %s", exc)
            result["health"] = {"available": False, "error": str(exc)}
            result["status"] = "error"

    if prompt:
        try:
            from bedrock_scanner import BedrockScanner
            scanner = BedrockScanner()
            start = _time.perf_counter()
            scan_result = scanner.scan(prompt)
            elapsed = round((_time.perf_counter() - start) * 1000)
            result["scan_result"] = scan_result
            result["scan_latency_ms"] = elapsed
            if "status" not in result:
                result["status"] = "ok"
        except Exception as exc:
            LOG.warning("Bedrock scan test failed: %s", exc)
            result["scan_error"] = str(exc)
            if "status" not in result:
                result["status"] = "error"

        if INPUT_SCANNER is not None:
            try:
                tier1_verdict = await INPUT_SCANNER.scan_prompt(prompt)
                result["tier1_result"] = {
                    "action": tier1_verdict.action,
                    "threat_type": tier1_verdict.threat_type,
                    "confidence": tier1_verdict.confidence,
                    "detail": tier1_verdict.detail,
                    "tier": tier1_verdict.tier,
                }
                result["pipeline_note"] = (
                    "In the live gateway, Tier 1 runs first. "
                    "If Tier 1 blocks, Bedrock (Tier 2) is skipped."
                )
            except Exception as exc:
                LOG.warning("Tier 1 scan in bedrock-test failed: %s", exc)

    if not check_health and not prompt:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "Provide 'check_health' or 'prompt'."},
        )

    return JSONResponse(content=result)


@app.get(
    "/v1/admin/circuit-breaker-state",
    summary="List circuit breaker states (all models with Redis keys)",
    tags=["Admin"],
)
async def admin_circuit_breaker_state(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not CIRCUIT_BREAKER or not REDIS_CLIENT:
        return JSONResponse(content={"models": [], "enabled": False})

    models: set[str] = set()
    cursor = 0
    while True:
        cursor, keys = await REDIS_CLIENT.scan(cursor, match="circuit:state:*", count=100)
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            models.add(key_str.replace("circuit:state:", ""))
        if cursor == 0:
            break

    import time as _time

    window = int(_time.time()) // 60
    results = []
    for model in sorted(models):
        status = await CIRCUIT_BREAKER.check(model)
        error_count = int(await REDIS_CLIENT.get(f"circuit:errors:{model}:{window}") or 0)
        total_count = int(await REDIS_CLIENT.get(f"circuit:total:{model}:{window}") or 0)
        results.append({
            "model": model,
            "state": status.state.value,
            "error_rate": round(error_count / total_count, 3) if total_count > 0 else 0.0,
            "error_count": error_count,
            "total_requests": total_count,
            "opened_at": status.opened_at,
            "should_block": status.should_block,
        })

    return JSONResponse(content={"models": results, "enabled": True})


@app.post(
    "/v1/admin/circuit-breaker-trigger",
    summary="Record simulated errors to open the circuit (dev/ops)",
    tags=["Admin"],
)
async def admin_circuit_breaker_trigger(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not CIRCUIT_BREAKER:
        return JSONResponse(status_code=503, content={"error": "Circuit breaker not initialized"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    model = body.get("model", "")
    error_count = min(int(body.get("error_count", 10)), 50)
    error_type = body.get("error_type", "simulated_error")
    for _ in range(error_count):
        await CIRCUIT_BREAKER.record_error(model, error_type)
    status = await CIRCUIT_BREAKER.check(model)
    return JSONResponse(content={
        "model": model,
        "errors_injected": error_count,
        "state": status.state.value,
        "should_block": status.should_block,
    })


@app.post(
    "/v1/admin/circuit-breaker-reset",
    summary="Clear circuit breaker Redis keys for a model",
    tags=["Admin"],
)
async def admin_circuit_breaker_reset(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not REDIS_CLIENT:
        return JSONResponse(status_code=503, content={"error": "Redis not available"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    model = body.get("model", "")
    cursor = 0
    deleted = 0
    while True:
        cursor, keys = await REDIS_CLIENT.scan(cursor, match=f"circuit:*:{model}*", count=100)
        if keys:
            await REDIS_CLIENT.delete(*keys)
            deleted += len(keys)
        if cursor == 0:
            break
    return JSONResponse(content={"model": model, "keys_deleted": deleted, "state": "closed"})


# --- Phase 1 F-3.1: admin proxy for RAG collection management ------------
# Per-tenant `/v1/rag/collections` requires an org Bearer key (per-org RBAC).
# The control plane cannot retrieve the raw key (only SHA-256 hash is
# stored). These admin variants take ``project_id`` explicitly, are gated
# by ``_require_admin_role`` (which accepts the GATEWAY_INTERNAL_API_KEY),
# and reuse the same vector-client logic. Downstream RBAC therefore lives
# in the Django proxy layer (IsAdminOrSuperuser).
def _strip_namespace(name: str, prefix: str) -> str:
    if isinstance(name, str) and name.startswith(prefix):
        return name[len(prefix):]
    return name


@app.get(
    "/v1/admin/rag/collections",
    summary="List vector DB collections for a given project (admin)",
    tags=["Admin"],
)
async def admin_rag_list_collections(request: Request, project_id: str = ""):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not VECTOR_CLIENTS:
        return JSONResponse(status_code=503, content={"error": "No vector clients configured."})
    project_id = (project_id or "").strip()
    if not project_id:
        return JSONResponse(status_code=400, content={"error": "Missing 'project_id' query parameter."})

    prefix = f"{project_id}__"
    result: dict[str, list[str]] = {}
    for provider_name, client in VECTOR_CLIENTS.items():
        try:
            if hasattr(client, "list_collections"):
                cols = await client.list_collections(project_id=project_id)
                result[provider_name] = [_strip_namespace(n, prefix) for n in (cols or [])]
            else:
                result[provider_name] = []
        except Exception as exc:
            LOG.warning("admin_rag_list_collections: %s failed: %s", provider_name, exc)
            result[provider_name] = []
    return JSONResponse(content={"project_id": project_id, "collections": result})


@app.post(
    "/v1/admin/rag/collections",
    summary="Create a vector DB collection for a given project (admin)",
    tags=["Admin"],
)
async def admin_rag_create_collection(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not VECTOR_CLIENTS:
        return JSONResponse(status_code=503, content={"error": "No vector clients configured."})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    project_id = (body.get("project_id") or "").strip()
    collection_name = (body.get("collection") or "").strip()
    vector_db_type = (body.get("vector_db_type") or "pinecone").strip()
    if not project_id:
        return JSONResponse(status_code=400, content={"error": "Missing 'project_id'."})
    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(status_code=403, content={
            "error": "forbidden",
            "message": "Invalid collection identifier. Use plain names without tenant prefixes.",
            "code": "rag_namespace_violation",
        })

    client, _ = _resolve_vector_client(vector_db_type, org_id=None)
    if client is None:
        return JSONResponse(status_code=400, content={"error": f"Unknown provider: {vector_db_type}"})
    try:
        if hasattr(client, "create_collection"):
            namespaced = await client.create_collection(collection_name=collection_name, project_id=project_id)
        else:
            namespaced = f"{project_id}__{collection_name}"
        return JSONResponse(content={
            "status": "created", "collection": collection_name,
            "namespaced_name": namespaced, "provider": vector_db_type,
            "project_id": project_id,
        })
    except Exception as exc:
        LOG.exception("admin_rag_create_collection failed: %s", collection_name)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.delete(
    "/v1/admin/rag/collections",
    summary="Delete a vector DB collection for a given project (admin)",
    tags=["Admin"],
)
async def admin_rag_delete_collection(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not VECTOR_CLIENTS:
        return JSONResponse(status_code=503, content={"error": "No vector clients configured."})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    project_id = (body.get("project_id") or "").strip()
    collection_name = (body.get("collection") or "").strip()
    vector_db_type = (body.get("vector_db_type") or "pinecone").strip()
    if not project_id:
        return JSONResponse(status_code=400, content={"error": "Missing 'project_id'."})
    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(status_code=403, content={
            "error": "forbidden",
            "message": "Invalid collection identifier. Use plain names without tenant prefixes.",
            "code": "rag_namespace_violation",
        })

    client = VECTOR_CLIENTS.get(vector_db_type)
    if client is None:
        client, _ = _resolve_vector_client(vector_db_type, org_id=None)
    if client is None:
        return JSONResponse(status_code=400, content={"error": f"Unknown provider: {vector_db_type}"})
    try:
        if hasattr(client, "delete_collection"):
            ok = await client.delete_collection(collection_name=collection_name, project_id=project_id)
        else:
            ok = False
        return JSONResponse(content={
            "status": "deleted" if ok else "not_found",
            "collection": collection_name,
            "provider": vector_db_type,
            "project_id": project_id,
        })
    except Exception as exc:
        LOG.exception("admin_rag_delete_collection failed: %s", collection_name)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get(
    "/v1/admin/logs",
    summary="Stream gateway logs via SSE",
    tags=["Admin"],
)
async def admin_logs(request: Request, service: str = "all", level: str = "DEBUG"):
    """Stream real-time gateway logs as Server-Sent Events."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        from log_buffer import LOG_BUFFER
    except ImportError:
        from .log_buffer import LOG_BUFFER

    async def event_generator():
        try:
            async for event in LOG_BUFFER.subscribe(
                service_filter=service,
                level_filter=level,
            ):
                yield event
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Bedrock Dedicated Log Endpoints ──────────────────────────────────────────

@app.get(
    "/v1/admin/bedrock-logs",
    summary="Recent Bedrock logs (JSON)",
    description=(
        "Returns the most recent Bedrock log entries from the in-memory ring buffer. "
        "Use `limit` to control how many entries (max 500), and `level` to filter "
        "by minimum severity (DEBUG, INFO, WARNING, ERROR)."
    ),
    tags=["Admin", "Bedrock"],
)
async def admin_bedrock_logs(request: Request, limit: int = 100, level: str = "DEBUG"):
    """Return recent Bedrock logs from the in-memory ring buffer."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        from bedrock_logger import BEDROCK_LOG_RING
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={"error": "bedrock_logger not available"},
        )
    entries = BEDROCK_LOG_RING.recent(limit=min(limit, 500), level_filter=level)
    return JSONResponse(content={
        "count": len(entries),
        "total_logged": BEDROCK_LOG_RING.counter,
        "level_filter": level,
        "entries": entries,
    })


@app.get(
    "/v1/admin/bedrock-logs/stream",
    summary="Stream Bedrock logs via SSE (live)",
    description=(
        "Server-Sent Events stream of Bedrock-only logs. "
        "Connect with EventSource or curl to watch Bedrock operations in real-time. "
        "Use `level` query param to set minimum severity (DEBUG, INFO, WARNING, ERROR)."
    ),
    tags=["Admin", "Bedrock"],
)
async def admin_bedrock_logs_stream(request: Request, level: str = "DEBUG"):
    """Stream real-time Bedrock logs as Server-Sent Events."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        from bedrock_logger import BEDROCK_LOG_RING
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={"error": "bedrock_logger not available"},
        )

    import json as _json

    level_priority = {
        "DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4,
    }
    min_level = level_priority.get(level.upper(), 0)

    async def bedrock_event_generator():
        last_seen = BEDROCK_LOG_RING.counter
        yield ": connected to bedrock log stream\n\n"
        heartbeat_counter = 0

        while True:
            current = BEDROCK_LOG_RING.counter
            if current > last_seen:
                new_count = current - last_seen
                items = list(BEDROCK_LOG_RING._buf)
                new_items = items[-new_count:] if new_count <= len(items) else items

                for item in new_items:
                    item_level = level_priority.get(item.level.upper(), 0)
                    if item_level < min_level:
                        continue
                    yield f"data: {_json.dumps(item.to_dict(), default=str)}\n\n"

                last_seen = current
                heartbeat_counter = 0
            else:
                heartbeat_counter += 1
                if heartbeat_counter >= 30:  # 15s heartbeat
                    yield ": heartbeat\n\n"
                    heartbeat_counter = 0

            await asyncio.sleep(0.5)

    return StreamingResponse(
        bedrock_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get(
    "/v1/admin/bedrock-logs/file",
    summary="Download Bedrock log file",
    description="Returns the current bedrock.log file content for download or inspection.",
    tags=["Admin", "Bedrock"],
)
async def admin_bedrock_logs_file(request: Request, tail: int = 200):
    """Return the last N lines of the bedrock.log file."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    import os as _os
    log_dir = _os.getenv("BEDROCK_LOG_DIR", "/var/log/bedrock")
    log_path = _os.path.join(log_dir, "bedrock.log")

    if not _os.path.exists(log_path):
        return JSONResponse(
            status_code=404,
            content={"error": "bedrock.log not found", "path": log_path},
        )

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail_lines = lines[-tail:] if len(lines) > tail else lines
        return JSONResponse(content={
            "file": log_path,
            "total_lines": len(lines),
            "returned_lines": len(tail_lines),
            "lines": [line.rstrip("\n") for line in tail_lines],
        })
    except OSError as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )


# ─────────────────────────────────────────────────────────────────────
# Lightweight policy-check endpoint — Phase 1 hot-path benchmark surface.
#
def _ambiguous_policy_block_response(*, org_slug: str) -> JSONResponse:
    """Fail-closed when policy engine returns block without matched rules."""
    LOG.error(
        "Policy check returned block without matched rules (org=%s); fail-closed",
        org_slug,
    )
    METRICS["blocked"] += 1
    return JSONResponse(
        status_code=503,
        content={
            "error": "service_unavailable",
            "message": (
                "Policy engine returned an ambiguous block. "
                "Request denied (fail-closed)."
            ),
            "code": "policy_ambiguous_block",
        },
    )


def _should_block_tier1_verdict(verdict, org_config: dict) -> bool:
    """Return True when a tier-1 scan verdict should block the request."""
    injection_threshold = org_config.get("prompt_injection_threshold", 0.80)
    _INJECTION_THREAT_TYPES = {"prompt_injection", "jailbreak", "goal_hijacking"}
    _is_injection = verdict.threat_type in _INJECTION_THREAT_TYPES
    _should_block = (
        verdict.action == "block"
        and verdict.threat_type not in ("pii", "secret")
    )
    if _is_injection:
        _should_block = (
            _should_block
            and org_config.get("scan_block_on_injection", True)
            and verdict.confidence >= injection_threshold
        )
    return _should_block


async def _policy_check_tier1_scan_block(
    *,
    prompt: str,
    org_config: dict,
    request: Request,
    auth_ctx,
    model: str = "",
) -> JSONResponse | None:
    """Run tier-1 input scan for /v1/policy/check; return 403 JSON if blocked."""
    if INPUT_SCANNER is None or not org_config.get("input_scan_enabled", True) or not prompt:
        return None
    try:
        org_slug = getattr(auth_ctx, "org_slug", "") if auth_ctx else ""
        verdict = await INPUT_SCANNER.scan_prompt(prompt, org_slug=org_slug)
    except Exception as exc:
        LOG.warning("Tier-1 scan failed on /v1/policy/check: %s", exc)
        return None
    if not _should_block_tier1_verdict(verdict, org_config):
        return None
    _emit_telemetry(
        status_code=403,
        event_type="input_blocked",
        model=model or "",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=getattr(auth_ctx, "project_id", "") or "",
        key_prefix=getattr(auth_ctx, "key_prefix", "") or "",
        action="block",
        risk_score=verdict.confidence,
        threat_type=verdict.threat_type,
        compliance_tags=org_config.get("compliance_frameworks", []),
        pipeline_stage="query",
        metadata={
            "detail": verdict.detail,
            "confidence": verdict.confidence,
            "matched_patterns": verdict.matched_patterns,
            "endpoint": "/v1/policy/check",
        },
    )
    METRICS["blocked"] += 1
    tier_label = {
        "tier_1": "Tier 1 regex",
        "tier_1_5": "Tier 1.5 fuzzy",
        "tier_1_6": "Tier 1.6 semantic",
        "tier_2": "Tier 2 ML",
    }.get(verdict.tier, verdict.tier or "tier_1")
    return _build_block_response(
        403,
        f"{verdict.tier or 'tier_1'}_{verdict.threat_type}",
        _build_zeroshield_metadata(
            action="block",
            reason=f"Input blocked by {tier_label} scanner.",
            detection_tier=verdict.tier or "tier_1",
            threat_type=verdict.threat_type,
            confidence=verdict.confidence,
            matched_patterns=verdict.matched_patterns,
            original_prompt=prompt,
            detail=verdict.detail,
        ),
    )


# Exercises the full hot path (auth → per-key TPM → org TPM → tier-1 scan →
# cached policy evaluation) WITHOUT forwarding to an LLM. This isolates the
# firewall overhead for p99 measurement under load (target: < 50 ms p99).
# ─────────────────────────────────────────────────────────────────────
@app.post(
    "/v1/policy/check",
    summary="Evaluate a prompt against compiled policies (no LLM call)",
    tags=["Policy"],
)
async def policy_check_endpoint(request: Request):
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Missing or invalid API key."},
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    prompt = (body.get("prompt") or "")[:8000]
    if not prompt and isinstance(body, dict) and body.get("messages"):
        prompt = _extract_prompt_from_messages(body.get("messages"))[:8000]
    response_text = (body.get("response") or "")[:8000]

    # Per-key TPM
    rate_limit_tpm = getattr(auth_ctx, "rate_limit_tpm", 0) or 0
    policy_est_tokens = _estimate_request_tokens(
        prompt or "",
        int(body.get("max_tokens") or 0) if isinstance(body, dict) else 0,
    )
    if RATE_LIMITER is not None and rate_limit_tpm:
        allowed, current = await RATE_LIMITER.check_rate_limit(
            auth_ctx.key_hash, rate_limit_tpm, policy_est_tokens,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "scope": "key",
                    "current": current,
                    "limit": rate_limit_tpm,
                },
                headers={"Retry-After": "60"},
            )

    # Per-org TPM (new in Phase 1 hardening)
    org_tpm = 0
    if CONFIG_SYNC is not None and auth_ctx.org_slug:
        try:
            org_cfg = CONFIG_SYNC.get_config(auth_ctx.org_slug) or {}
            org_tpm = int(org_cfg.get("org_tpm_limit", 0) or 0)
        except Exception:
            org_tpm = 0
    if RATE_LIMITER is not None and org_tpm:
        policy_est_tokens = _estimate_request_tokens(
            prompt or "",
            int(body.get("max_tokens") or 0) if isinstance(body, dict) else 0,
        )
        org_allowed, org_current = await RATE_LIMITER.check_org_rate_limit(
            auth_ctx.org_slug, org_tpm, policy_est_tokens,
        )
        if not org_allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "scope": "org",
                    "current": org_current,
                    "limit": org_tpm,
                },
                headers={"Retry-After": "60"},
            )

    org_cfg: dict = {}
    if CONFIG_SYNC is not None and auth_ctx.org_slug:
        try:
            org_cfg = CONFIG_SYNC.get_config(auth_ctx.org_slug) or {}
        except Exception:
            org_cfg = {}

    tier1_block = await _policy_check_tier1_scan_block(
        prompt=prompt,
        org_config=org_cfg,
        request=request,
        auth_ctx=auth_ctx,
        model=body.get("model") if isinstance(body, dict) else "",
    )
    if tier1_block is not None:
        return tier1_block

    import time as _t
    _t0 = _t.perf_counter()
    status, result = _policy_check_cached(
        prompt=prompt,
        response_text=response_text,
        user_id=getattr(auth_ctx, "user_id", None),
        endpoint_id=None,
        project_id=None,
        risk_score=None,
        model=body.get("model"),
        org_slug=auth_ctx.org_slug or "default",
    )
    # Emit telemetry for /v1/policy/check evaluations (Phase 1 hot-path observability).
    try:
        _latency_ms = (_t.perf_counter() - _t0) * 1000.0
        _action = "block" if status in (403, 451) else "allow"
        _decision = (result or {}).get("decision") if isinstance(result, dict) else None
        _REQUEST_ORG_ID.set(getattr(auth_ctx, "organization_id", None))
        _REQUEST_ORG_SLUG.set(getattr(auth_ctx, "org_slug", "") or "")
        _REQUEST_SOURCE_IP.set(request.client.host if request.client else "")
        _REQUEST_METHOD.set("POST")
        _emit_telemetry(
            status_code=status,
            event_type="policy_check",
            model=body.get("model") or "",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=getattr(auth_ctx, "project_id", "") or "",
            key_prefix=getattr(auth_ctx, "key_prefix", "") or "",
            latency_ms=_latency_ms,
            action=_action,
            pipeline_stage="query",
            metadata={"decision": _decision, "endpoint": "/v1/policy/check"},
        )
    except Exception:
        pass
    return JSONResponse(status_code=status, content=result)


@app.get(
    "/health",
    summary="Health check",
    description="Liveness probe. Returns the gateway status and registered agent ID.",
    tags=["Health"],
    responses={
        200: {
            "description": "Gateway is healthy",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    }
                }
            },
        },
    },
)
async def health():
    # Phase 0 D-G1-v3: signing-key misconfig check MUST run before any
    # CONFIG access — health is also called from probes during startup
    # before _bootstrap() initialises CONFIG, and we want a deterministic
    # 503 with structured reason rather than a 500 from None.get().
    if signing_enforced() and _get_signing_key() is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "reason": "policy_signing_key_missing",
                "agent_id": AGENT_ID,
            },
        )
    policy_info = {}
    if POLICY_SYNC is not None:
        policy_info = {
            "policy_cache_loaded": POLICY_SYNC.is_loaded,
            "policy_cache_version": POLICY_SYNC.version,
            "policy_count": POLICY_SYNC.policy_count,
        }
    config_info = {}
    if CONFIG_SYNC is not None and CONFIG is not None:
        config_info = {
            "config_sync_loaded": CONFIG_SYNC.is_loaded,
            "firewall_enabled": CONFIG.get("firewall_enabled", True),
            "enforcement_mode": CONFIG.get("enforcement_mode", "block"),
        }
    vector_info = {}
    if VECTOR_POLICY_SYNC is not None:
        vector_info = {
            "vector_policy_cache_loaded": VECTOR_POLICY_SYNC.is_loaded,
            "vector_policy_cache_version": VECTOR_POLICY_SYNC.version,
            "vector_policy_count": VECTOR_POLICY_SYNC.policy_count,
            "vector_clients": list(VECTOR_CLIENTS.keys()),
        }
    runtime_metrics = {}
    if REDIS_CLIENT is not None:
        try:
            runtime_metrics["telemetry_queue_depth"] = int(await REDIS_CLIENT.llen("telemetry:events"))
        except Exception:
            runtime_metrics["telemetry_queue_depth"] = -1
    cache_required = CONFIG.get("policy_cache_require_loaded", True) if CONFIG is not None else True
    cache_loaded = bool(policy_info.get("policy_cache_loaded", False)) if policy_info else False
    if cache_required and not cache_loaded:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "reason": "policy_cache_unavailable",
                "agent_id": AGENT_ID,
                **policy_info,
                **config_info,
                **vector_info,
                **runtime_metrics,
            },
        )

    return {
        "status": "ok",
        "agent_id": AGENT_ID,
        **policy_info,
        **config_info,
        **vector_info,
        **runtime_metrics,
    }


@app.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "Prometheus text-format metrics for the AI Mesh Firewall gateway. "
        "Exposes counters/gauges/histograms only — no PII in label values. "
        "Should be bound to an internal/scraper-only network in production."
    ),
    tags=["Health"],
    include_in_schema=False,
)
async def prometheus_metrics():
    try:
        from .metrics import render_latest
    except ImportError:
        from metrics import render_latest  # type: ignore[no-redef]
    body, content_type = render_latest()
    from starlette.responses import Response
    return Response(content=body, media_type=content_type)


@app.get(
    "/v1/models",
    summary="List available models",
    description="Returns the list of LLM models configured in the gateway (OpenAI-compatible format).",
    tags=["Models"],
    responses={
        200: {
            "description": "List of available models",
            "content": {
                "application/json": {
                    "example": {
                        "object": "list",
                        "data": [
                            {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
                        ],
                    }
                }
            },
        },
    },
)
async def list_models(request: Request):
    if LLM_ROUTER is None:
        return JSONResponse(content={"object": "list", "data": []})
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(content={"object": "list", "data": []})

    models = LLM_ROUTER.get_model_list()

    # Filter by org if auth context is available (optional auth — endpoint stays in EXCLUDED_PATHS)
    org_slug = auth_ctx.org_slug if auth_ctx else ""
    if org_slug and CONFIG_SYNC:
        org_routing = CONFIG_SYNC.get_model_routing(org_slug)
        if org_routing:
            eligible = _filter_inference_eligible_models(org_routing)
            allowed = _routing_identity_set(eligible)
            models = [m for m in models if m.get("id") in allowed]
        else:
            models = []

    return JSONResponse(content={"object": "list", "data": models})


class _HealthEndpointAccessFilter(logging.Filter):
    """Suppress uvicorn access-log lines for health-check endpoints.

    Prevents readiness-probe polling (/health, /api/health, etc.) from
    drowning out real request traffic in the gateway access log.
    """

    _SUPPRESSED = frozenset(
        ["/health", "/api/health", "/readyz", "/livez", "/ping", "/_health"]
    )

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        msg = record.getMessage()
        return not any(path in msg for path in self._SUPPRESSED)


_LOGGING_CONFIGURED = False


def _configure_logging() -> None:
    """Configure root logging level from GATEWAY_LOG_LEVEL env var.

    Falls back to INFO when the env var is absent (production default).
    When GATEWAY_LOG_JSON=true, emits every record as a compact JSON line
    so structured log aggregators (Loki, CloudWatch, Datadog) can parse them.

    Idempotent: safe to call from both ``main()`` and module import. This
    matters because the production container launches the app via
    ``uvicorn ai_mesh_gateway.main:app``, which imports the ``app`` object
    directly and never calls ``main()`` — without the import-time call below,
    application loggers (``gateway.*``) would only emit via Python's bare
    ``lastResort`` handler (WARNING+ only, no formatting), silently dropping
    INFO diagnostics such as the stdio-adapter's stderr-tail capture.
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    raw_level = os.environ.get("GATEWAY_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, raw_level, logging.INFO)

    use_json = os.environ.get("GATEWAY_LOG_JSON", "false").lower() in ("1", "true", "yes")

    if use_json:
        # Inline JSON formatter — no extra dependency required.
        import json as _json

        class _JSONFormatter(logging.Formatter):
            SERVICE = "gateway"

            def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
                payload = {
                    "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f+00:00"),
                    "level": record.levelname,
                    "service": self.SERVICE,
                    "component": record.name,
                    "module": record.module,
                    "lineno": record.lineno,
                    "message": record.getMessage(),
                }
                if record.exc_info:
                    payload["exc_info"] = self.formatException(record.exc_info)
                return _json.dumps(payload, ensure_ascii=False)

        fmt = _JSONFormatter()
    else:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Apply health-endpoint suppression to the uvicorn access logger so that
    # readiness-probe requests (/health, /api/health, etc.) don't pollute the log
    # stream even when --log-level debug is active.
    uvicorn_access_log = logging.getLogger("uvicorn.access")
    uvicorn_access_log.addFilter(_HealthEndpointAccessFilter())

    _LOGGING_CONFIGURED = True


# Configure logging at import time so application loggers are wired even when the
# app is launched via ``uvicorn ai_mesh_gateway.main:app`` (which never calls
# ``main()``). The idempotent guard above makes the later ``main()`` call a no-op.
try:
    _configure_logging()
except Exception:  # pragma: no cover — logging setup must never break startup
    pass


def main():
    _configure_logging()
    cfg = load_config()
    uvicorn.run(app, host="0.0.0.0", port=cfg["port"])


if __name__ == "__main__":
    main()

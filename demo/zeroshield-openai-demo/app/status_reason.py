"""Normalized user-facing status/reason contract for demo UI surfaces."""
from __future__ import annotations

from typing import Any

from app.pipeline import build_pipeline_view

# Canonical reason codes shared across Chat, Files, MCP, Routing, Guardrails.
REASON_BLOCKED_POLICY = "blocked_policy"
REASON_REDACTED_ALLOWED = "redacted_allowed"
REASON_ROUTING_NOT_CONFIGURED = "routing_not_configured"
REASON_ROUTING_UNSATISFIABLE = "routing_unsatisfiable"
REASON_SINGLE_ROUTE = "single_route"
REASON_FILE_UNREADABLE = "file_unreadable"
REASON_FILE_CONTENT_BLOCKED = "file_content_blocked"
REASON_FILE_CONTENT_REDACTED = "file_content_redacted"
REASON_MCP_EVENT_BLOCKED = "mcp_event_blocked"
REASON_MCP_CONTEXT_REDACTED = "mcp_context_redacted"
REASON_GUARDRAIL_INPUT_BLOCKED = "guardrail_input_blocked"
REASON_GUARDRAIL_OUTPUT_REDACTED = "guardrail_output_redacted"
REASON_GUARDRAIL_OUTPUT_BLOCKED = "guardrail_output_blocked"
REASON_GATEWAY_ERROR = "gateway_error"
REASON_PROVIDER_AUTH = "provider_auth_failed"
REASON_RAG_ACCESS_DENIED = "rag_access_denied"
REASON_RAG_VECTOR_UNAVAILABLE = "rag_vector_unavailable"
REASON_ALLOWED = "allowed"

_REASON_COPY: dict[str, dict[str, str]] = {
    REASON_BLOCKED_POLICY: {
        "label": "Blocked by policy",
        "message": "ZeroShield blocked this request during input analysis before it reached the model.",
        "next_step": "Remove jailbreak or injection patterns and try again.",
    },
    REASON_REDACTED_ALLOWED: {
        "label": "Redacted and allowed",
        "message": "Sensitive content was redacted; the request continued through the gateway.",
        "next_step": "Review the pipeline panel to confirm redaction stages.",
    },
    REASON_ROUTING_NOT_CONFIGURED: {
        "label": "Routing not configured",
        "message": "Dynamic routing is disabled or no eligible models are in the routing pool.",
        "next_step": "Enable routing in Module 1.5 and connect at least one active model with a valid key.",
    },
    REASON_ROUTING_UNSATISFIABLE: {
        "label": "Routing policy unsatisfied",
        "message": "No connected model satisfies the requested sensitivity or compliance policy.",
        "next_step": "Lower sensitivity, connect a compliant model, or widen the org routing pool.",
    },
    REASON_SINGLE_ROUTE: {
        "label": "Single route available",
        "message": "Only one eligible model is configured, so auto-routing selected it.",
        "next_step": "Add more models with different sensitivity tiers to demonstrate routing.",
    },
    REASON_FILE_UNREADABLE: {
        "label": "File not understandable",
        "message": "The uploaded file could not be parsed into text for gateway analysis.",
        "next_step": "Upload a PDF, DOCX, TXT, or CSV file with readable text content.",
    },
    REASON_FILE_CONTENT_BLOCKED: {
        "label": "Document content blocked",
        "message": "ZeroShield blocked sensitive or policy-violating content found in the uploaded document.",
        "next_step": "Remove secrets, PII, or injection patterns from the document and try again.",
    },
    REASON_FILE_CONTENT_REDACTED: {
        "label": "Document content redacted",
        "message": "Sensitive content in the uploaded document was redacted; analysis continued through the gateway.",
        "next_step": "Review the summary and pipeline panel to confirm what was redacted.",
    },
    REASON_MCP_EVENT_BLOCKED: {
        "label": "MCP context blocked",
        "message": "MCP background context was blocked by the gateway during input analysis.",
        "next_step": "Remove secrets, PII, or injection patterns from MCP context and retry.",
    },
    REASON_MCP_CONTEXT_REDACTED: {
        "label": "MCP context redacted",
        "message": "Sensitive content in MCP context was redacted; the request continued through the gateway.",
        "next_step": "Review the pipeline panel to confirm which MCP fields were redacted.",
    },
    REASON_GUARDRAIL_INPUT_BLOCKED: {
        "label": "Prompt blocked at input scan",
        "message": "ZeroShield blocked this prompt during input analysis before it reached the model.",
        "next_step": "Remove jailbreak, injection, or policy-violating patterns and try again.",
    },
    REASON_GUARDRAIL_OUTPUT_REDACTED: {
        "label": "Model output redacted",
        "message": "The model responded, but ZeroShield redacted sensitive content in the output.",
        "next_step": "Review the pipeline panel to confirm output guard stages.",
    },
    REASON_GUARDRAIL_OUTPUT_BLOCKED: {
        "label": "Model output blocked",
        "message": "The model produced a response, but ZeroShield blocked it during output validation.",
        "next_step": "Adjust the prompt or org output policy if this block was unexpected.",
    },
    REASON_GATEWAY_ERROR: {
        "label": "Gateway error",
        "message": "The gateway could not complete this request.",
        "next_step": "Check the request ID in the pipeline panel and gateway logs.",
    },
    REASON_PROVIDER_AUTH: {
        "label": "Provider key rejected",
        "message": "The upstream model provider rejected the API key configured for this model connection.",
        "next_step": "Reconnect the model in Model Connections with a valid provider key, or set OPENAI_API_KEY and run scripts/bootstrap_openai_models.py.",
    },
    REASON_RAG_ACCESS_DENIED: {
        "label": "RAG access denied",
        "message": "This API key cannot access the requested vector collection.",
        "next_step": "Run scripts/bootstrap_rag.py once to create the demo_knowledge policy, then recompile vector policies.",
    },
    REASON_RAG_VECTOR_UNAVAILABLE: {
        "label": "Vector store unavailable",
        "message": "The gateway could not reach the configured vector database for this collection.",
        "next_step": "Start Chroma (docker compose --profile chroma up -d chromadb) and run scripts/bootstrap_rag.py to point the org provider at http://chromadb:8000.",
    },
    REASON_ALLOWED: {
        "label": "Allowed",
        "message": "Request passed all security stages.",
        "next_step": "",
    },
}


def _policy_error_code(zs: dict, pipe: dict) -> str:
    return str(
        zs.get("threat_type") or zs.get("code") or pipe.get("code") or ""
    ).strip().lower()


def _rag_error_code(zs: dict, pipe: dict, *, message: str = "") -> str:
    code = _policy_error_code(zs, pipe)
    if code:
        return code
    raw = str(message or zs.get("detail") or zs.get("message") or "").lower()
    if "no policy for collection" in raw or "rag_access_denied" in raw:
        return "rag_access_denied"
    if any(
        token in raw
        for token in (
            "vector retrieval failed",
            "ingest_failed",
            "rag_pipeline_blocked",
            "could not connect to a chroma",
            "no vector provider configured",
        )
    ):
        return "rag_vector_unavailable"
    return ""


def _routing_error_code(zs: dict, pipe: dict, *, support: dict | None = None, message: str = "") -> str:
    support_issue = str((support or {}).get("issue") or "").strip().lower()
    if support_issue == "routing_unsatisfiable":
        return "routing_unsatisfiable"
    code = _policy_error_code(zs, pipe)
    if code in ("compliance_routing_unsatisfiable", "routing_unsatisfiable"):
        return code
    merged = str(message or zs.get("detail") or zs.get("message") or "").lower()
    if "compliance_routing_unsatisfiable" in merged or "no model satisfies" in merged:
        return "routing_unsatisfiable"
    return ""


def _is_policy_block(zs: dict, pipe: dict, *, message: str = "") -> bool:
    action = str(pipe.get("action") or zs.get("action") or "").lower()
    blocked_by = str(pipe.get("blocked_by") or zs.get("blocked_by") or "").lower()
    code = _policy_error_code(zs, pipe)
    msg = str(message or zs.get("detail") or "").lower()
    if code in ("content_filter", "content_blocked", "prompt_injection"):
        return True
    if action != "block":
        return False
    if blocked_by in ("input_scan", "policy", "output_guardrail", ""):
        return True
    if "blocked due to security" in msg or "prompt injection" in msg or "jailbreak" in msg:
        return True
    return False


def _routing_dict(zs: dict) -> dict:
    routing = zs.get("routing")
    return routing if isinstance(routing, dict) else {}


def _output_guard_action(zs: dict, pipe: dict) -> str:
    blocked_by = str(pipe.get("blocked_by") or zs.get("blocked_by") or "").lower()
    action = str(pipe.get("action") or zs.get("action") or "").lower()
    stages = pipe.get("stages") if isinstance(pipe.get("stages"), list) else []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("id") or "") != "output_guardrail":
            continue
        stage_action = str(stage.get("action") or "").lower()
        if stage_action in ("block", "redact", "flag"):
            return stage_action
    if blocked_by == "output_guardrail":
        return action or "block"
    return ""


def derive_status_reason(
    *,
    zeroshield: dict | None = None,
    pipeline: dict | None = None,
    error: bool = False,
    status: int | None = None,
    support: dict | None = None,
    fallback: str | None = None,
    context: str = "chat",
    file_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Map gateway/demo metadata to a normalized status_reason payload."""
    zs = zeroshield or {}
    pipe = pipeline or {}
    routing = _routing_dict(zs)
    action = str(pipe.get("action") or zs.get("action") or "").lower()
    blocked_by = str(pipe.get("blocked_by") or zs.get("blocked_by") or "").lower()
    decision_source = str(
        routing.get("decision_source") or zs.get("decision_source") or ""
    ).lower()
    candidate_count = routing.get("candidate_count")
    threat = str(zs.get("threat_type") or pipe.get("category") or "").lower()

    if file_errors and error:
        copy = dict(_REASON_COPY[REASON_FILE_UNREADABLE])
        copy["details"] = file_errors
        return {"code": REASON_FILE_UNREADABLE, **copy}

    if context == "files" and action in ("redact", "flag"):
        copy = dict(_REASON_COPY[REASON_FILE_CONTENT_REDACTED])
        detail = str(zs.get("detail") or pipe.get("routing_reason") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_FILE_CONTENT_REDACTED, **copy}

    if context == "files" and (
        action == "block"
        or _is_policy_block(zs, pipe, message=str(zs.get("detail") or pipe.get("routing_reason") or ""))
    ):
        copy = dict(_REASON_COPY[REASON_FILE_CONTENT_BLOCKED])
        detail = str(zs.get("detail") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_FILE_CONTENT_BLOCKED, **copy}

    if context == "guardrail":
        output_action = _output_guard_action(zs, pipe)
        if output_action == "block" or (action == "block" and blocked_by == "output_guardrail"):
            copy = dict(_REASON_COPY[REASON_GUARDRAIL_OUTPUT_BLOCKED])
            detail = str(zs.get("detail") or pipe.get("routing_reason") or (support.get("plain_text") if support else "") or "").strip()
            if detail:
                copy["message"] = detail
            return {"code": REASON_GUARDRAIL_OUTPUT_BLOCKED, **copy}
        if output_action in ("redact", "flag") or (
            action in ("redact", "flag") and blocked_by == "output_guardrail"
        ):
            copy = dict(_REASON_COPY[REASON_GUARDRAIL_OUTPUT_REDACTED])
            detail = str(zs.get("detail") or pipe.get("routing_reason") or "").strip()
            if detail:
                copy["message"] = detail
            return {"code": REASON_GUARDRAIL_OUTPUT_REDACTED, **copy}
        if (
            action == "block"
            or _is_policy_block(zs, pipe, message=str(zs.get("detail") or pipe.get("routing_reason") or ""))
            or (error and int(status or 0) in (400, 403, 422))
        ):
            copy = dict(_REASON_COPY[REASON_GUARDRAIL_INPUT_BLOCKED])
            detail = str(zs.get("detail") or (support.get("plain_text") if support else "") or "").strip()
            if detail:
                copy["message"] = detail
            return {"code": REASON_GUARDRAIL_INPUT_BLOCKED, **copy}

    rag_code = _rag_error_code(
        zs,
        pipe,
        message=str(zs.get("detail") or zs.get("message") or (support.get("plain_text") if support else "") or ""),
    )
    if rag_code == "rag_access_denied" or (context == "rag" and rag_code == "rag_access_denied"):
        copy = dict(_REASON_COPY[REASON_RAG_ACCESS_DENIED])
        detail = str(zs.get("detail") or zs.get("message") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_RAG_ACCESS_DENIED, **copy}
    if context == "rag" and (
        rag_code == "rag_vector_unavailable"
        or rag_code in ("ingest_failed", "rag_pipeline_blocked", "retrieval_error", "vector_provider_unconfigured")
    ):
        copy = dict(_REASON_COPY[REASON_RAG_VECTOR_UNAVAILABLE])
        detail = str(zs.get("detail") or zs.get("message") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_RAG_VECTOR_UNAVAILABLE, **copy}

    if context == "mcp" and action in ("redact", "flag"):
        copy = dict(_REASON_COPY[REASON_MCP_CONTEXT_REDACTED])
        detail = str(zs.get("detail") or pipe.get("routing_reason") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_MCP_CONTEXT_REDACTED, **copy}

    if context == "mcp" and (action == "block" or _is_policy_block(zs, pipe, message=str(zs.get("detail") or pipe.get("routing_reason") or ""))):
        copy = dict(_REASON_COPY[REASON_MCP_EVENT_BLOCKED])
        detail = str(zs.get("detail") or pipe.get("routing_reason") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_MCP_EVENT_BLOCKED, **copy}

    if _is_policy_block(zs, pipe, message=str(zs.get("detail") or pipe.get("routing_reason") or "")):
        copy = dict(_REASON_COPY[REASON_BLOCKED_POLICY])
        detail = str(zs.get("detail") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_BLOCKED_POLICY, **copy}

    if action == "block":
        copy = dict(_REASON_COPY[REASON_BLOCKED_POLICY])
        detail = str(zs.get("detail") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_BLOCKED_POLICY, **copy}

    routing_code = _routing_error_code(
        zs,
        pipe,
        support=support if isinstance(support, dict) else None,
        message=str(zs.get("detail") or (support.get("plain_text") if support else "") or ""),
    )
    if context == "routing" and routing_code == "routing_unsatisfiable":
        copy = dict(_REASON_COPY[REASON_ROUTING_UNSATISFIABLE])
        detail = str(zs.get("detail") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_ROUTING_UNSATISFIABLE, **copy}

    support_issue = str((support or {}).get("issue") or "").strip().lower()
    if error and support_issue == "provider_auth":
        copy = dict(_REASON_COPY[REASON_PROVIDER_AUTH])
        if support and support.get("plain_text"):
            copy["message"] = support["plain_text"]
            if support.get("next_step"):
                copy["next_step"] = support["next_step"]
        return {"code": REASON_PROVIDER_AUTH, **copy}

    if error and int(status or 0) in (400, 401, 403, 422, 429):
        copy = dict(_REASON_COPY[REASON_GATEWAY_ERROR])
        if support and support.get("plain_text"):
            copy["message"] = support["plain_text"]
            if support.get("next_step"):
                copy["next_step"] = support["next_step"]
        return {"code": REASON_GATEWAY_ERROR, **copy}

    if action in ("redact", "flag") and blocked_by != "input_scan":
        copy = dict(_REASON_COPY[REASON_REDACTED_ALLOWED])
        return {"code": REASON_REDACTED_ALLOWED, **copy}

    routing_status = str(routing.get("routing_status") or "").lower()
    if routing_status == "single_route" or decision_source == "weighted_fastpath":
        if candidate_count == 1 or "single candidate" in str(routing.get("routing_reason") or "").lower():
            return {"code": REASON_SINGLE_ROUTE, **_REASON_COPY[REASON_SINGLE_ROUTE]}

    if decision_source in ("no_routing_models", "routing_disabled") or routing_status == "routing_not_configured":
        return {"code": REASON_ROUTING_NOT_CONFIGURED, **_REASON_COPY[REASON_ROUTING_NOT_CONFIGURED]}

    if routing_code == "routing_unsatisfiable":
        copy = dict(_REASON_COPY[REASON_ROUTING_UNSATISFIABLE])
        detail = str(zs.get("detail") or (support.get("plain_text") if support else "") or "").strip()
        if detail:
            copy["message"] = detail
        return {"code": REASON_ROUTING_UNSATISFIABLE, **copy}

    if error or int(status or 0) >= 400:
        copy = dict(_REASON_COPY[REASON_GATEWAY_ERROR])
        if support and support.get("plain_text"):
            copy["message"] = support["plain_text"]
            if support.get("next_step"):
                copy["next_step"] = support["next_step"]
        if fallback:
            copy["message"] = f"{copy['message']} (recovery: {fallback})"
        return {"code": REASON_GATEWAY_ERROR, **copy}

    return {"code": REASON_ALLOWED, **_REASON_COPY[REASON_ALLOWED]}


def attach_status_reason(view: dict[str, Any], *, context: str = "chat", file_errors: list[str] | None = None) -> dict[str, Any]:
    """Attach status_reason to a demo client response view."""
    if not isinstance(view, dict):
        return view
    pipeline = view.get("pipeline") if isinstance(view.get("pipeline"), dict) else None
    if pipeline is None:
        pipeline = build_pipeline_view(zeroshield=view.get("zeroshield"))
        view["pipeline"] = pipeline
    reason = derive_status_reason(
        zeroshield=view.get("zeroshield"),
        pipeline=pipeline,
        error=bool(view.get("error")),
        status=view.get("status"),
        support=view.get("support") if isinstance(view.get("support"), dict) else None,
        fallback=view.get("fallback"),
        context=context,
        file_errors=file_errors,
    )
    view["status_reason"] = reason
    if isinstance(pipeline, dict):
        pipeline["status_reason"] = reason
    return view


def attach_files_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach customer-facing status_reason to a files/analyze API payload."""
    if not isinstance(payload, dict):
        return payload
    analysis = payload.get("analysis")
    file_warnings = payload.get("file_warnings") if isinstance(payload.get("file_warnings"), list) else []

    if not analysis:
        manifest = payload.get("files_manifest") if isinstance(payload.get("files_manifest"), list) else []
        file_errors = [
            f"{m.get('name')}: {m.get('error', 'unreadable')}"
            for m in manifest
            if isinstance(m, dict) and m.get("status") == "error"
        ]
        return attach_status_reason(
            payload,
            context="files",
            file_errors=file_errors or None,
        )

    attach_status_reason(analysis, context="files")
    payload["analysis"] = analysis
    reason = dict(analysis.get("status_reason") or {})
    if file_warnings and reason.get("code") == REASON_ALLOWED:
        reason["details"] = file_warnings
        reason["message"] = "Analysis completed; some uploaded files could not be read."
    payload["status_reason"] = reason
    return payload

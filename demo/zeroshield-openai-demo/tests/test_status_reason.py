"""Unit tests for demo status/reason contract."""
from __future__ import annotations

from app.status_reason import (
    REASON_ALLOWED,
    REASON_BLOCKED_POLICY,
    REASON_FILE_CONTENT_BLOCKED,
    REASON_FILE_UNREADABLE,
    REASON_GUARDRAIL_INPUT_BLOCKED,
    REASON_GUARDRAIL_OUTPUT_REDACTED,
    REASON_MCP_CONTEXT_REDACTED,
    REASON_MCP_EVENT_BLOCKED,
    REASON_ROUTING_NOT_CONFIGURED,
    REASON_ROUTING_UNSATISFIABLE,
    REASON_SINGLE_ROUTE,
    attach_status_reason,
    derive_status_reason,
)


def test_blocked_policy_from_content_filter():
    reason = derive_status_reason(
        zeroshield={"action": "block", "threat_type": "content_filter", "detail": "Request blocked due to security policy"},
        pipeline={"action": "block"},
        error=True,
        status=400,
    )
    assert reason["code"] == REASON_BLOCKED_POLICY


def test_blocked_policy_from_input_scan():
    reason = derive_status_reason(
        zeroshield={"action": "block", "blocked_by": "input_scan", "detail": "Prompt injection detected"},
        pipeline={"action": "block", "blocked_by": "input_scan"},
        error=True,
        status=403,
    )
    assert reason["code"] == REASON_BLOCKED_POLICY


def test_single_route_from_weighted_fastpath():
    reason = derive_status_reason(
        zeroshield={
            "action": "allow",
            "routing": {
                "decision_source": "weighted_fastpath",
                "candidate_count": 1,
                "routing_reason": "Single candidate — no routing decision required.",
            },
        },
        pipeline={"action": "allow"},
    )
    assert reason["code"] == REASON_SINGLE_ROUTE


def test_routing_not_configured():
    reason = derive_status_reason(
        zeroshield={
            "routing": {
                "decision_source": "no_routing_models",
                "routing_status": "routing_not_configured",
            }
        }
    )
    assert reason["code"] == REASON_ROUTING_NOT_CONFIGURED


def test_file_unreadable_on_total_parse_failure():
    reason = derive_status_reason(
        file_errors=["report.pdf: unsupported format"],
        error=True,
        context="files",
    )
    assert reason["code"] == REASON_FILE_UNREADABLE
    assert "report.pdf" in reason["details"][0]


def test_file_warnings_do_not_override_allowed_without_error():
    reason = derive_status_reason(
        zeroshield={"action": "allow"},
        pipeline={"action": "allow"},
        file_errors=["bad.exe: unsupported"],
        error=False,
        context="files",
    )
    assert reason["code"] == REASON_ALLOWED


def test_file_content_blocked_with_files_context():
    reason = derive_status_reason(
        zeroshield={"action": "block", "blocked_by": "input_scan", "detail": "SSN in document"},
        pipeline={"action": "block", "blocked_by": "input_scan"},
        context="files",
    )
    assert reason["code"] == REASON_FILE_CONTENT_BLOCKED
    assert reason["code"] != REASON_BLOCKED_POLICY


def test_guardrail_input_blocked_with_context():
    reason = derive_status_reason(
        zeroshield={"action": "block", "blocked_by": "input_scan", "detail": "Prompt injection detected"},
        pipeline={"action": "block", "blocked_by": "input_scan"},
        error=True,
        status=403,
        context="guardrail",
    )
    assert reason["code"] == REASON_GUARDRAIL_INPUT_BLOCKED
    assert reason["code"] != REASON_BLOCKED_POLICY


def test_guardrail_output_redacted_with_context():
    reason = derive_status_reason(
        zeroshield={"action": "redact", "blocked_by": "output_guardrail", "detail": "PII redacted in output"},
        pipeline={
            "action": "redact",
            "blocked_by": "output_guardrail",
            "stages": [{"id": "output_guardrail", "action": "redact"}],
        },
        context="guardrail",
    )
    assert reason["code"] == REASON_GUARDRAIL_OUTPUT_REDACTED


def test_guardrail_safe_allow():
    reason = derive_status_reason(
        zeroshield={"action": "allow"},
        pipeline={"action": "allow", "stages": [{"id": "output_guardrail", "action": "allow"}]},
        context="guardrail",
    )
    assert reason["code"] == REASON_ALLOWED


def test_attach_status_reason_on_view():
    view = attach_status_reason({"content": "ok", "zeroshield": {"action": "allow"}})
    assert view["status_reason"]["code"] == REASON_ALLOWED
    assert view["pipeline"]["status_reason"]["code"] == REASON_ALLOWED


def test_rag_access_denied_reason():
    from app.status_reason import REASON_RAG_ACCESS_DENIED

    reason = derive_status_reason(
        zeroshield={"code": "rag_access_denied", "message": "Access denied: no policy for collection 'demo_knowledge'."},
        error=True,
        status=403,
        context="rag",
    )
    assert reason["code"] == REASON_RAG_ACCESS_DENIED


def test_rag_vector_unavailable_reason():
    from app.status_reason import REASON_RAG_VECTOR_UNAVAILABLE

    reason = derive_status_reason(
        zeroshield={"code": "rag_pipeline_blocked", "message": "RAG pipeline blocked at retriever: Vector retrieval failed."},
        error=True,
        status=403,
        context="rag",
    )
    assert reason["code"] == REASON_RAG_VECTOR_UNAVAILABLE


def test_mcp_context_blocked_reason():
    reason = derive_status_reason(
        zeroshield={"action": "block", "blocked_by": "input_scan", "detail": "SSN detected in MCP context"},
        pipeline={"action": "block", "blocked_by": "input_scan"},
        context="mcp",
    )
    assert reason["code"] == REASON_MCP_EVENT_BLOCKED


def test_mcp_context_redacted_reason():
    reason = derive_status_reason(
        zeroshield={"action": "redact", "detail": "PII redacted in MCP context"},
        pipeline={"action": "redact"},
        context="mcp",
    )
    assert reason["code"] == REASON_MCP_CONTEXT_REDACTED


def test_mcp_block_beats_generic_policy_when_context_is_mcp():
    reason = derive_status_reason(
        zeroshield={"action": "block", "threat_type": "content_filter", "detail": "Blocked"},
        pipeline={"action": "block", "code": "content_filter"},
        context="mcp",
    )
    assert reason["code"] == REASON_MCP_EVENT_BLOCKED
    assert reason["code"] != REASON_BLOCKED_POLICY


def test_routing_unsatisfiable_from_support_hint():
    reason = derive_status_reason(
        zeroshield={"action": "error", "detail": "compliance_routing_unsatisfiable"},
        pipeline={"action": "error"},
        error=True,
        status=403,
        support={
            "issue": "routing_unsatisfiable",
            "plain_text": "No model currently matches this sensitivity/compliance requirement.",
        },
        context="routing",
    )
    assert reason["code"] == REASON_ROUTING_UNSATISFIABLE


def test_routing_not_configured_with_routing_context():
    reason = derive_status_reason(
        zeroshield={
            "routing": {
                "decision_source": "no_routing_models",
                "routing_status": "routing_not_configured",
            }
        },
        context="routing",
    )
    assert reason["code"] == REASON_ROUTING_NOT_CONFIGURED


def test_single_route_with_routing_context():
    reason = derive_status_reason(
        zeroshield={
            "action": "allow",
            "routing": {
                "decision_source": "weighted_fastpath",
                "candidate_count": 1,
                "routing_reason": "Single candidate — no routing decision required.",
            },
        },
        pipeline={"action": "allow"},
        context="routing",
    )
    assert reason["code"] == REASON_SINGLE_ROUTE


def test_provider_auth_failed_on_upstream_401():
    from app.status_reason import REASON_PROVIDER_AUTH

    reason = derive_status_reason(
        zeroshield={"action": "error", "code": "401", "threat_type": "401"},
        pipeline={"action": "error", "code": "401", "requested_model": "gpt-4o-mini"},
        error=True,
        status=401,
        support={
            "issue": "provider_auth",
            "plain_text": "ZeroShield accepted your gateway key, but the upstream model provider rejected the BYOK credential.",
            "next_step": "Reconnect the model in Model Connections.",
        },
        context="chat",
    )
    assert reason["code"] == REASON_PROVIDER_AUTH

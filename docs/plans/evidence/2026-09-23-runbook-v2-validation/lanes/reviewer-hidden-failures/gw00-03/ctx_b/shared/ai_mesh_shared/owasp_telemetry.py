"""
Normalize gateway/worker telemetry into OWASP LLM / MCP / Agentic vector codes
for the security dashboard (OwaspStatsView).
"""

from __future__ import annotations

import re
from typing import Any

_OWASP_CODE_RE = re.compile(r"^(LLM|MCP|AGENTIC)\d{2}$", re.IGNORECASE)

# Canonical threat_type → OWASP code (lowercase keys).
THREAT_TYPE_TO_OWASP: dict[str, str] = {
  # LLM Top 10
    "prompt_injection": "LLM01",
    "injection": "LLM01",
    "insecure_output": "LLM02",
    "toxicity": "LLM02",
    "training_data_poisoning": "LLM03",
    "jailbreak": "LLM02",
    "dos": "LLM04",
    "supply_chain": "LLM05",
    "data_leakage": "LLM06",
    "pii": "LLM06",
    "secret": "LLM06",
    "sensitive_disclosure": "LLM06",
    "credential": "LLM06",
    "ip_leakage": "LLM06",
    "plugin_vulnerability": "LLM07",
    "rag_poisoning": "LLM08",
    "excessive_agency": "LLM08",
    "overreliance": "LLM09",
    "hallucination": "LLM09",
    "model_theft": "LLM10",
    "model_not_allowed": "LLM09",
    "high_risk_actor": "LLM10",
    "blocked_keyword": "LLM01",
    "policy_violation": "LLM01",
    "access_violation": "LLM09",
    "kill_switch": "LLM09",
    "bedrock": "LLM01",
    "bedrock_redact": "LLM06",
    "bedrock_degraded": "",
    # MCP tool security
    "tool_overreach": "MCP01",
    "mcp_overreach": "MCP01",
    "unauthorized_api": "MCP02",
    "unauthorized_api_call": "MCP02",
    "context_overflow": "MCP03",
    "mcp_injection": "MCP04",
    "tool_injection": "MCP04",
    "privilege_escalation_tool": "MCP05",
    "data_exfiltration_tool": "MCP06",
    "data_exfiltration": "MCP06",
    "recursive_tool": "MCP07",
    "recursive_tool_calls": "MCP07",
    "parameter_injection": "MCP08",
    "tool_parameter_injection": "MCP08",
    "unsafe_tool_combination": "MCP09",
    "tool_state_manipulation": "MCP10",
    # Agentic AI
    "goal_hijacking": "AGENTIC01",
    "infinite_loop": "AGENTIC02",
    "infinite_loops": "AGENTIC02",
    "privilege_escalation": "AGENTIC03",
    "resource_abuse": "AGENTIC04",
    "uncontrolled_resource": "AGENTIC04",
    "identity_spoofing": "AGENTIC05",
    "agent_impersonation": "AGENTIC05",
    "state_manipulation": "AGENTIC06",
    "action_misalignment": "AGENTIC06",
    "multi_agent_collusion": "AGENTIC07",
    "cascading_hallucination": "AGENTIC07",
    "planning_injection": "AGENTIC08",
    "context_leakage": "AGENTIC08",
    "memory_poisoning": "AGENTIC09",
    "rogue_agent": "AGENTIC09",
    "tool_chain_exploitation": "AGENTIC10",
    "audit_evasion": "AGENTIC10",
}

OWASP_ENFORCED_ACTIONS = frozenset({"block", "redact", "rewrite"})


def normalize_owasp_code(raw: Any) -> str:
    """Return uppercase LLM01 / MCP02 / AGENTIC03 or empty string."""
    if raw is None:
        return ""
    code = str(raw).strip().upper().replace(" ", "")
    if _OWASP_CODE_RE.match(code):
        return code
    return ""


def _append_code(codes: list[str], raw: Any) -> None:
    code = normalize_owasp_code(raw)
    if code:
        codes.append(code)


def _codes_from_findings(findings: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(findings, list):
        return out
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        for field in ("rule_id", "owasp_code", "code", "vector"):
            _append_code(out, finding.get(field))
    return out


def resolve_owasp_codes(
    threat_type: str = "",
    extra: dict | None = None,
    *,
    event_type: str = "",
) -> list[str]:
    """
    Collect all OWASP vector codes for an enforcement event.

    Priority: explicit owasp_codes / rule_ids / Bedrock raw_findings, then
    pass-through LLMxx|MCPxx|AGENTICxx threat_type, then THREAT_TYPE_TO_OWASP map.
    """
    extra = extra if isinstance(extra, dict) else {}
    codes: list[str] = []

    explicit = extra.get("owasp_codes")
    if isinstance(explicit, list):
        for item in explicit:
            _append_code(codes, item)
    elif explicit:
        _append_code(codes, explicit)

    _append_code(codes, extra.get("owasp_code"))

    for rid in extra.get("rule_ids") or []:
        _append_code(codes, rid)

    codes.extend(_codes_from_findings(extra.get("raw_findings")))

    for scan_key in ("owasp_llm", "owasp_mcp", "owasp_agentic"):
        scan_block = extra.get(scan_key)
        if isinstance(scan_block, dict):
            for item in scan_block.get("findings") or scan_block.get("vectors") or []:
                if isinstance(item, dict):
                    _append_code(codes, item.get("code") or item.get("rule_id"))
                else:
                    _append_code(codes, item)

    tt = (threat_type or "").strip()
    direct = normalize_owasp_code(tt)
    if direct:
        codes.append(direct)
    elif tt:
        mapped = THREAT_TYPE_TO_OWASP.get(tt.lower()) or THREAT_TYPE_TO_OWASP.get(tt)
        if mapped:
            codes.append(mapped)

    # MCP mirror events may only set source + owasp_code on extra
    if extra.get("source") == "mcp_scan" and not any(c.startswith("MCP") for c in codes):
        _append_code(codes, extra.get("owasp_code"))

    # Deduplicate, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for code in codes:
        if code and code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def primary_owasp_code(codes: list[str]) -> str:
    """First code in list (for legacy owasp_code metadata field)."""
    return codes[0] if codes else ""


def is_owasp_enforced(action: str) -> bool:
    """True when the enforcement action counts toward blocked/coverage metrics."""
    return (action or "").strip().lower() in OWASP_ENFORCED_ACTIONS

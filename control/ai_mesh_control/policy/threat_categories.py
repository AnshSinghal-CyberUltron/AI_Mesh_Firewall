"""
OWASP code to threat category/subcategory mapping for EnforcementEvent metadata (Phase 4).
"""

# LLM Top 10 (for get_threat_from_scan_result)
LLM_CODE_TO_CATEGORY = {
    "LLM01": "Prompt Injection",
    "LLM02": "Insecure Output Handling",
    "LLM03": "Training Data Poisoning",
    "LLM04": "Model Denial of Service",
    "LLM05": "Supply Chain Vulnerabilities",
    "LLM06": "Sensitive Information Disclosure",
    "LLM07": "Insecure Plugin Design",
    "LLM08": "Excessive Agency",
    "LLM09": "Overreliance",
    "LLM10": "Model Theft",
}

# MCP Top 10
MCP_CODE_TO_CATEGORY = {
    "MCP01": "MCP Tool Overreach",
    "MCP02": "Unauthorized API Calls",
    "MCP03": "Context Overflow",
    "MCP04": "Tool Injection",
    "MCP05": "Privilege Escalation via Tools",
    "MCP06": "Data Exfiltration via Tools",
    "MCP07": "Recursive Tool Calls",
    "MCP08": "Tool Parameter Injection",
    "MCP09": "Unsafe Tool Combinations",
    "MCP10": "Tool State Manipulation",
}
# Normalize internal scan keys to canonical OWASP codes (e.g. LLM06_input/LLM06_output -> LLM06)
LLM_CODE_NORMALIZE = {
    "LLM06_input": "LLM06",
    "LLM06_output": "LLM06",
}

# Alias for UI (e.g. "Plugin Injection")
MCP_CODE_TO_SUBCATEGORY = {
    "MCP01": "OWASP MCP01",
    "MCP02": "OWASP MCP02",
    "MCP03": "OWASP MCP03",
    "MCP04": "Plugin Injection",
    "MCP05": "OWASP MCP05",
    "MCP06": "OWASP MCP06",
    "MCP07": "OWASP MCP07",
    "MCP08": "OWASP MCP08",
    "MCP09": "OWASP MCP09",
    "MCP10": "OWASP MCP10",
}

# Agentic AI Top 10
AGENTIC_CODE_TO_CATEGORY = {
    "AGENTIC01": "Goal Hijacking",
    "AGENTIC02": "Infinite Loops",
    "AGENTIC03": "Privilege Escalation",
    "AGENTIC04": "Uncontrolled Resource Consumption",
    "AGENTIC05": "Agent Impersonation",
    "AGENTIC06": "State Manipulation",
    "AGENTIC07": "Multi-Agent Collusion",
    "AGENTIC08": "Planning Injection",
    "AGENTIC09": "Memory Poisoning",
    "AGENTIC10": "Tool Chain Exploitation",
}
AGENTIC_CODE_TO_SUBCATEGORY = {
    "AGENTIC01": "OWASP Agentic01",
    "AGENTIC02": "OWASP Agentic02",
    "AGENTIC03": "OWASP Agentic03",
    "AGENTIC04": "OWASP Agentic04",
    "AGENTIC05": "OWASP Agentic05",
    "AGENTIC06": "OWASP Agentic06",
    "AGENTIC07": "OWASP Agentic07",
    "AGENTIC08": "OWASP Agentic08",
    "AGENTIC09": "OWASP Agentic09",
    "AGENTIC10": "OWASP Agentic10",
}


def _first_detected_from_scan_results(scan_results_dict):
    """Return first (code, category, subcategory) from scan_results where detected."""
    if not isinstance(scan_results_dict, dict):
        return None
    for code, result in scan_results_dict.items():
        if code == "summary":
            continue
        if getattr(result, "detected", False) or (isinstance(result, dict) and result.get("detected")):
            return code
    return None


def _all_detected_from_scan_results(scan_results_dict, normalize_fn=None):
    """
    Return list of all codes from scan_results where detected.
    normalize_fn: optional callable(code) -> normalized code (e.g. LLM06_input -> LLM06).
    Deduplicates by normalized code.
    """
    if not isinstance(scan_results_dict, dict):
        return []
    seen = set()
    codes = []
    for code, result in scan_results_dict.items():
        if code == "summary":
            continue
        if getattr(result, "detected", False) or (isinstance(result, dict) and result.get("detected")):
            norm = normalize_fn(code) if normalize_fn else code
            if norm is None:
                norm = code
            if norm not in seen:
                seen.add(norm)
                codes.append(norm)
    return codes


def get_threat_from_scan_result(scan_result):
    """
    From ScanResult.threats_detected (detection_results dict), return (source, owasp_code, category, subcategory)
    for the first LLM, agentic, or MCP threat. Prefer agentic then MCP then LLM.
    """
    detection = getattr(scan_result, "threats_detected", None) or {}
    # Agentic
    agentic_data = detection.get("owasp_agentic")
    if isinstance(agentic_data, dict):
        scan_res = agentic_data.get("scan_results", agentic_data)
        code = _first_detected_from_scan_results(scan_res)
        if code:
            category = AGENTIC_CODE_TO_CATEGORY.get(code, code)
            subcategory = AGENTIC_CODE_TO_SUBCATEGORY.get(code, code)
            return ("agentic_scan", code, category, subcategory)
    # MCP
    mcp_data = detection.get("owasp_mcp")
    if isinstance(mcp_data, dict):
        scan_res = mcp_data.get("scan_results", mcp_data)
        code = _first_detected_from_scan_results(scan_res)
        if code:
            category = MCP_CODE_TO_CATEGORY.get(code, code)
            subcategory = MCP_CODE_TO_SUBCATEGORY.get(code, code)
            return ("mcp_scan", code, category, subcategory)
    # OWASP LLM
    llm_data = detection.get("owasp_llm")
    if isinstance(llm_data, dict):
        scan_res = llm_data.get("scan_results", llm_data)
        code = _first_detected_from_scan_results(scan_res)
        if code:
            code = LLM_CODE_NORMALIZE.get(code, code)
            category = LLM_CODE_TO_CATEGORY.get(code, code)
            subcategory = f"OWASP {code}"
            return ("security_scan", code, category, subcategory)
    return (None, None, None, None)


def get_all_threats_from_scan_result(scan_result):
    """
    From ScanResult.threats_detected, return (source, owasp_codes, primary_category, subcategory)
    where owasp_codes is a list of ALL detected codes (normalized) for the primary family.
    Prefer agentic then MCP then LLM. Each event is counted under every matching vector.
    """
    detection = getattr(scan_result, "threats_detected", None) or {}
    # Agentic
    agentic_data = detection.get("owasp_agentic")
    if isinstance(agentic_data, dict):
        scan_res = agentic_data.get("scan_results", agentic_data)
        codes = _all_detected_from_scan_results(scan_res)
        if codes:
            primary = codes[0]
            category = AGENTIC_CODE_TO_CATEGORY.get(primary, primary)
            subcategory = AGENTIC_CODE_TO_SUBCATEGORY.get(primary, primary)
            return ("agentic_scan", codes, category, subcategory)
    # MCP
    mcp_data = detection.get("owasp_mcp")
    if isinstance(mcp_data, dict):
        scan_res = mcp_data.get("scan_results", mcp_data)
        codes = _all_detected_from_scan_results(scan_res)
        if codes:
            primary = codes[0]
            category = MCP_CODE_TO_CATEGORY.get(primary, primary)
            subcategory = MCP_CODE_TO_SUBCATEGORY.get(primary, primary)
            return ("mcp_scan", codes, category, subcategory)
    # OWASP LLM
    llm_data = detection.get("owasp_llm")
    if isinstance(llm_data, dict):
        scan_res = llm_data.get("scan_results", llm_data)
        codes = _all_detected_from_scan_results(scan_res, normalize_fn=LLM_CODE_NORMALIZE.get)
        if codes:
            primary = codes[0]
            category = LLM_CODE_TO_CATEGORY.get(primary, primary)
            subcategory = f"OWASP {primary}"
            return ("security_scan", codes, category, subcategory)
    return (None, [], None, None)


# Human-readable incident titles for Investigation & Forensics UI
CATEGORY_TO_INCIDENT_TITLE = {
    "Prompt Injection": "Prompt Injection Attack",
    "Data Exfiltration via Tools": "Data Exfiltration Attempt",
    "MCP Tool Overreach": "MCP Tool Overreach",
    "Unauthorized API Calls": "Unauthorized API Attempt",
    "Context Overflow": "Context Overflow",
    "Tool Injection": "Tool Injection",
    "Plugin Injection": "Plugin Injection",
    "Privilege Escalation via Tools": "Privilege Escalation Attempt",
    "Recursive Tool Calls": "Recursive Tool Calls",
    "Tool Parameter Injection": "Tool Parameter Injection",
    "Unsafe Tool Combinations": "Unsafe Tool Combinations",
    "Tool State Manipulation": "Tool State Manipulation",
    "Goal Hijacking": "Goal Hijacking Attempt",
    "Infinite Loops": "Agent Infinite Loop",
    "Privilege Escalation": "Privilege Escalation",
    "Uncontrolled Resource Consumption": "Resource Abuse",
    "Agent Impersonation": "Agent Impersonation",
    "State Manipulation": "State Manipulation",
    "Multi-Agent Collusion": "Multi-Agent Collusion",
    "Planning Injection": "Planning Injection",
    "Memory Poisoning": "Memory Poisoning",
    "Tool Chain Exploitation": "Tool Chain Exploitation",
}


def get_incident_title(category, subcategory, source):
    """Return human-readable incident title for forensics UI."""
    if category and isinstance(category, str):
        return CATEGORY_TO_INCIDENT_TITLE.get(category, category)
    if subcategory and isinstance(subcategory, str):
        return CATEGORY_TO_INCIDENT_TITLE.get(subcategory, subcategory)
    if source == "security_scan":
        return "Security threat detected"
    if source == "mcp_scan":
        return "MCP tool violation"
    if source == "agentic_scan":
        return "Agentic behavior violation"
    return "Policy event"

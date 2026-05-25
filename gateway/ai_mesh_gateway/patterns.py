"""
Shared regex patterns for PII, secrets, credentials, output guardrails.

Single source of truth for the gateway data plane. Both ``scanner.py``
and ``context_guard.py`` import from this module to eliminate duplication.

Provides:
- Cached regex compilation via ``compile_pattern()``
- Detection helpers: ``detect_pii()``, ``detect_secrets()``
- Output guardrail patterns: hallucination, IP leakage, credential exposure
- Redaction helper: ``redact_all()``
- Compliance tagging: ``COMPLIANCE_TAG_MAP``, ``get_compliance_tags()``
"""
from __future__ import annotations

import functools
import re
from typing import Dict, List


@functools.lru_cache(maxsize=512)
def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile and cache regex patterns for performance."""
    return re.compile(pattern, re.IGNORECASE)


PII_PATTERNS: Dict[str, str] = {
    "credit_card": r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "email": r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    "phone_us": r"\b(?:\+1[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b",
    "api_key_openai": r"\bsk-[a-zA-Z0-9]{32,}\b",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "github_token": r"\bghp_[a-zA-Z0-9]{36}\b",
    "private_key_header": r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
}

SECRET_PATTERNS: Dict[str, str] = {
    "password_assignment": r'password["\s:=]+\S+',
    "secret_assignment": r'secret["\s:=]+\S+',
    "token_assignment": r'token["\s:=]+[a-zA-Z0-9_\-\.]+',
}

PHI_PATTERNS: Dict[str, str] = {
    "medical_license": r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b",
    "medical_record": r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b",
    "insurance_id": r"\b(?:insurance\s+(?:id|number|#)|policy\s*#)\s*[:\s]*[A-Z0-9]{5,15}\b",
}

PCI_PATTERNS: Dict[str, str] = {
    "iban_code": r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b",
    "us_bank_number": r"\b(?:account|routing)\s*#?\s*[:\s]*\d{8,17}\b",
    "crypto_address": r"\b(?:0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})\b",
}

HALLUCINATION_PATTERNS: Dict[str, str] = {
    "uncertainty_hedge": (
        r"\b(?:I think|I believe|probably|possibly|"
        r"I'm not (?:sure|certain)|might be|could be|as far as I know)\b"
    ),
    "fabricated_citation": (
        r"(?:according to|as stated (?:in|by)|per the (?:study|report|paper|article))\s+"
        r"(?:(?:Dr\.\s+)?[A-Z][a-z]+ (?:et al\.?|and colleagues)|\[\d+\])"
    ),
    "confidence_disclaimer": (
        r"(?:I (?:cannot|can't) verify|"
        r"I do not have (?:access|information)|"
        r"this (?:may|might) not be (?:accurate|correct|up to date))"
    ),
    "contradictory_statement": (
        r"(?:however|but|on the other hand|conversely|"
        r"contrary to (?:what I (?:just )?said|the above|this))"
    ),
    "fabricated_statistic": (
        r"(?:approximately|roughly|about|nearly)\s+\d+(?:\.\d+)?%?\s+"
        r"(?:of|percent|per cent)"
    ),
    "nonexistent_entity": (
        r"(?:the\s+(?:University|Institute|Organization|Foundation|Agency)"
        r"\s+of\s+[A-Z][a-z]+\s+[A-Z][a-z]+)"
    ),
    "temporal_impossibility": (
        r"(?:as of\s+\d{4}|in\s+(?:19|20)\d{2})\s+.*?"
        r"(?:will|is going to|plans to|expects to)"
    ),
    "source_attribution_gap": (
        r"(?:research (?:shows|indicates|suggests|proves)|"
        r"studies (?:show|indicate|suggest|prove)|"
        r"experts (?:say|agree|believe))\b"
    ),
}

IP_LEAKAGE_PATTERNS: Dict[str, str] = {
    "internal_ipv4": (
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3})\b"
    ),
    "internal_hostname": r"\b(?:[a-z][a-z0-9-]+\.(?:internal|local|corp|intra|private|lan))\b",
    "file_path_unix": r"(?:/(?:home|var|etc|opt|usr|tmp|srv)/[a-zA-Z0-9_./-]{3,})",
    "file_path_windows": r"(?:[A-Z]:\\(?:Users|Windows|Program Files|temp|AppData)\\[a-zA-Z0-9_.\\ -]{3,})",
    "internal_url": (
        r"https?://(?:(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3})|"
        r"[a-z][a-z0-9-]+\.(?:internal|local|corp))[:/]"
    ),
}

CREDENTIAL_EXPOSURE_PATTERNS: Dict[str, str] = {
    "bearer_token": r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
    "basic_auth": r"Basic\s+[A-Za-z0-9+/=]{16,}",
    "exposed_password": r"(?:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
    "connection_string": r"(?:mongodb|mysql|postgres(?:ql)?|redis|amqp)://[^\s]{10,}",
    "private_key_block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
}

COMPLIANCE_TAG_MAP: Dict[str, List[str]] = {
    "credit_card": ["PCI-DSS"],
    "ssn": ["PII", "HIPAA"],
    "email": ["PII", "GDPR"],
    "phone_us": ["PII", "GDPR"],
    "api_key_openai": ["SECRET"],
    "aws_access_key": ["SECRET"],
    "github_token": ["SECRET"],
    "private_key_header": ["SECRET"],
    "password_assignment": ["SECRET"],
    "secret_assignment": ["SECRET"],
    "token_assignment": ["SECRET"],
    "medical_license": ["PHI", "HIPAA"],
    "medical_record": ["PHI", "HIPAA"],
    "insurance_id": ["PHI", "HIPAA"],
    "iban_code": ["PCI-DSS"],
    "us_bank_number": ["PCI-DSS"],
    "crypto_address": ["PCI-DSS"],
    "internal_ipv4": ["INFRA"],
    "internal_hostname": ["INFRA"],
    "file_path_unix": ["INFRA"],
    "file_path_windows": ["INFRA"],
    "internal_url": ["INFRA"],
    "bearer_token": ["SECRET", "SOC2"],
    "basic_auth": ["SECRET", "SOC2"],
    "exposed_password": ["SECRET", "SOC2"],
    "connection_string": ["SECRET", "SOC2"],
    "private_key_block": ["SECRET", "SOC2"],
}


def detect_pii(text: str) -> Dict[str, str]:
    """Detect PII, PHI, and PCI patterns in text. Returns dict of type -> matched value."""
    found: Dict[str, str] = {}
    for pii_type, pattern_str in PII_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[pii_type] = match.group(0)
    for phi_type, pattern_str in PHI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[phi_type] = match.group(0)
    for pci_type, pattern_str in PCI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[pci_type] = match.group(0)
    return found


def detect_secrets(text: str) -> Dict[str, str]:
    """Detect secret patterns in text. Returns dict of type -> matched value."""
    found: Dict[str, str] = {}
    for secret_type, pattern_str in SECRET_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[secret_type] = match.group(0)
    return found


# ── Smart partial-masking helpers ──────────────────────────────────────────────

def _mask_email(m: re.Match) -> str:
    """john@company.com → j***@c***.com"""
    full = m.group(0)
    try:
        local, domain_full = full.split("@", 1)
        domain_parts = domain_full.rsplit(".", 1)
        tld = domain_parts[1] if len(domain_parts) > 1 else ""
        domain_name = domain_parts[0]
        masked_local = (local[0] + "***") if local else "***"
        masked_domain = (domain_name[0] + "***") if domain_name else "***"
        return f"{masked_local}@{masked_domain}.{tld}"
    except Exception:
        return "[EMAIL]"


def _mask_credit_card(m: re.Match) -> str:
    """4111-1111-1111-1111 → ****-****-****-1111"""
    digits = re.sub(r"[\s\-]", "", m.group(0))
    return f"****-****-****-{digits[-4:]}"


def _mask_ssn(m: re.Match) -> str:
    """123-45-6789 → ***-**-6789"""
    raw = m.group(0)
    return f"***-**-{raw[-4:]}"


def _mask_phone(m: re.Match) -> str:
    """555-867-5309 → ***-***-5309"""
    digits = re.sub(r"[^\d]", "", m.group(0))
    return f"***-***-{digits[-4:]}"


def _mask_api_key(m: re.Match) -> str:
    """sk-abc...xyz → sk-****xyz"""
    s = m.group(0)
    dash_idx = s.find("-")
    prefix = s[: dash_idx + 1] if dash_idx != -1 else s[:3]
    return f"{prefix}****{s[-4:]}" if len(s) > len(prefix) + 8 else f"{prefix}****"


def _mask_aws_key(m: re.Match) -> str:
    """AKIA1234ABCD5678WXYZ → AKIA****WXYZ"""
    s = m.group(0)
    return f"AKIA****{s[-4:]}"


def _mask_github_token(m: re.Match) -> str:
    """ghp_abc...xyz → ghp_****xyz"""
    s = m.group(0)
    return f"ghp_****{s[-4:]}"


def _mask_secret_assignment(m: re.Match) -> str:
    """password="secret" → password="*** (keep key + separator, mask value)"""
    raw = m.group(0)
    sep_match = re.search(r'["\s:=]+', raw)
    if sep_match:
        return raw[: sep_match.end()] + "***"
    return raw[:4] + "***"


_PII_MASKERS = {
    "email": _mask_email,
    "credit_card": _mask_credit_card,
    "ssn": _mask_ssn,
    "phone_us": _mask_phone,
    "api_key_openai": _mask_api_key,
    "aws_access_key": _mask_aws_key,
    "github_token": _mask_github_token,
    "private_key_header": lambda m: "[PRIVATE_KEY]",
}

_SECRET_MASKERS = {
    "password_assignment": _mask_secret_assignment,
    "secret_assignment": _mask_secret_assignment,
    "token_assignment": _mask_secret_assignment,
}

_CREDENTIAL_MASKERS = {
    "bearer_token": lambda m: "Bearer [BEARER_TOKEN_REDACTED]",
    "basic_auth": lambda m: "Basic [BASIC_AUTH_REDACTED]",
    "exposed_password": lambda m: "password=[PASSWORD_REDACTED]",
    "connection_string": lambda m: "[CONNECTION_STRING_REDACTED]",
    "private_key_block": lambda m: "[PRIVATE_KEY_REDACTED]",
}


def redact_all(text: str) -> str:
    """Redact PII with smart partial masking; PHI/PCI use placeholder tags."""
    result = text
    for pii_type, pattern_str in PII_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _PII_MASKERS.get(pii_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{pii_type.upper()}_REDACTED]", result)
    for phi_type, pattern_str in PHI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        result = compiled.sub(f"[{phi_type.upper()}_REDACTED]", result)
    for pci_type, pattern_str in PCI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        result = compiled.sub(f"[{pci_type.upper()}_REDACTED]", result)
    for secret_type, pattern_str in SECRET_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _SECRET_MASKERS.get(secret_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{secret_type.upper()}_REDACTED]", result)
    for cred_type, pattern_str in CREDENTIAL_EXPOSURE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _CREDENTIAL_MASKERS.get(cred_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{cred_type.upper()}_REDACTED]", result)
    return result


def get_compliance_tags(pattern_keys: List[str]) -> List[str]:
    """
    Given a list of matched pattern keys (e.g. ['ssn', 'credit_card']),
    return deduplicated compliance framework tags.
    """
    tags: set[str] = set()
    for key in pattern_keys:
        mapped = COMPLIANCE_TAG_MAP.get(key)
        if mapped:
            tags.update(mapped)
    return sorted(tags)


def detect_hallucination_markers(text: str) -> Dict[str, str]:
    """Detect hallucination risk markers in text."""
    found: Dict[str, str] = {}
    for marker_type, pattern_str in HALLUCINATION_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[marker_type] = match.group(0)
    return found


def detect_ip_leakage(text: str) -> Dict[str, str]:
    """Detect internal IP/infrastructure leakage patterns in text."""
    found: Dict[str, str] = {}
    for leak_type, pattern_str in IP_LEAKAGE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[leak_type] = match.group(0)
    return found


def detect_credential_exposure(text: str) -> Dict[str, str]:
    """Detect credential exposure patterns in text."""
    found: Dict[str, str] = {}
    for cred_type, pattern_str in CREDENTIAL_EXPOSURE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[cred_type] = match.group(0)
    return found

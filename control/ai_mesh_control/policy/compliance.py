"""
Compliance framework tagging for audit events.

Maps threat types and matched pattern keys to regulatory framework
labels (HIPAA, PCI-DSS, GDPR, CCPA, SOC2) for use in EnforcementEvent
metadata.
"""

from __future__ import annotations

COMPLIANCE_FRAMEWORKS = {
    "pii": ["GDPR", "CCPA"],
    "ssn": ["PII", "HIPAA"],
    "credit_card": ["PCI-DSS"],
    "phi": ["HIPAA"],
    "email": ["GDPR", "CCPA"],
    "phone_us": ["GDPR", "CCPA"],
    "medical_record": ["HIPAA"],
    "insurance_id": ["HIPAA"],
    "bank_number": ["PCI-DSS"],
    "api_key_openai": ["SECRET", "SOC2"],
    "aws_access_key": ["SECRET", "SOC2"],
    "github_token": ["SECRET", "SOC2"],
    "private_key_header": ["SECRET", "SOC2"],
    "password_assignment": ["SECRET", "SOC2"],
    "secret_assignment": ["SECRET", "SOC2"],
    "token_assignment": ["SECRET", "SOC2"],
    "secret": ["SOC2"],
}


def get_compliance_tags(
    threat_type: str | None = None,
    matched_patterns: list[str] | None = None,
) -> list[str]:
    """
    Return deduplicated, sorted compliance framework tags for a given
    threat type and/or list of matched pattern keys.

    Examples::

        get_compliance_tags("pii", ["ssn", "credit_card"])
        # -> ["CCPA", "GDPR", "HIPAA", "PCI-DSS", "PII"]

        get_compliance_tags("secret")
        # -> ["SOC2"]
    """
    tags: set[str] = set()

    if threat_type:
        mapped = COMPLIANCE_FRAMEWORKS.get(threat_type)
        if mapped:
            tags.update(mapped)

    for key in matched_patterns or []:
        mapped = COMPLIANCE_FRAMEWORKS.get(key)
        if mapped:
            tags.update(mapped)

    return sorted(tags)

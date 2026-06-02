"""
Compliance tag catalog.

Defines ZeroShield's standard compliance tag set and human-readable metadata
used in audit annotations, frontend filters, and the ``ComplianceTag`` Django
model:

* ``GDPR-PII``  – EU General Data Protection Regulation: personal data.
* ``HIPAA-PHI`` – US Health Insurance Portability and Accountability Act:
  Protected Health Information.
* ``PCI-CARD``  – Payment Card Industry: cardholder data.
* ``SOC2-CONF`` – SOC 2 Confidentiality: secrets, tokens, internal IDs.
* ``ITAR``      – International Traffic in Arms Regulations (US export control).
* ``FERPA``     – Family Educational Rights and Privacy Act (student records).

This module is **import-cheap** (no detector/model imports) so it can be used
from both the control plane (Django) and the gateway (FastAPI).
"""

from __future__ import annotations


# Stable string codes used in audit metadata, frontend filters, and the
# ComplianceTag Django model. Order is preserved for UI display.
COMPLIANCE_TAG_CODES = (
    "GDPR-PII",
    "HIPAA-PHI",
    "PCI-CARD",
    "SOC2-CONF",
    "ITAR",
    "FERPA",
)


# Human-readable metadata, used to seed the ComplianceTag catalog on
# migration. Keep in sync with policy/migrations/0030_*.py.
COMPLIANCE_TAG_METADATA: dict[str, dict[str, str]] = {
    "GDPR-PII": {
        "label": "GDPR Personal Data",
        "regulation": "EU GDPR Art. 4(1)",
        "description": "Personally identifiable information of EU data subjects.",
        "severity": "high",
    },
    "HIPAA-PHI": {
        "label": "HIPAA Protected Health Information",
        "regulation": "45 CFR §164.514",
        "description": "Individually identifiable health information.",
        "severity": "high",
    },
    "PCI-CARD": {
        "label": "PCI Cardholder Data",
        "regulation": "PCI-DSS v4.0",
        "description": "Primary account numbers and related cardholder data.",
        "severity": "critical",
    },
    "SOC2-CONF": {
        "label": "SOC 2 Confidentiality",
        "regulation": "AICPA Trust Services Criteria",
        "description": "Secrets, tokens, internal credentials, and confidential identifiers.",
        "severity": "high",
    },
    "ITAR": {
        "label": "ITAR Controlled Technical Data",
        "regulation": "22 CFR §120-130",
        "description": "US export-controlled defense articles or technical data.",
        "severity": "critical",
    },
    "FERPA": {
        "label": "FERPA Education Records",
        "regulation": "20 U.S.C. § 1232g",
        "description": "Personally identifiable information from student education records.",
        "severity": "medium",
    },
}

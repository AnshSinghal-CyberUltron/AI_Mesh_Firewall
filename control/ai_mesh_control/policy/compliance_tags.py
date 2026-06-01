"""
Presidio entity → compliance tag mapping.

Bridges Microsoft Presidio entity types (e.g. ``EMAIL_ADDRESS``, ``US_SSN``,
``CREDIT_CARD``) to ZeroShield's standard compliance tag set
(DECISION-D Phase 1):

* ``GDPR-PII``  – EU General Data Protection Regulation: personal data.
* ``HIPAA-PHI`` – US Health Insurance Portability and Accountability Act:
  Protected Health Information.
* ``PCI-CARD``  – Payment Card Industry: cardholder data.
* ``SOC2-CONF`` – SOC 2 Confidentiality: secrets, tokens, internal IDs.
* ``ITAR``      – International Traffic in Arms Regulations (US export
  control). Not natively detected by Presidio; reserved for keyword/
  custom-recognizer extensions.
* ``FERPA``     – Family Educational Rights and Privacy Act. Reserved for
  custom recognizers (student IDs, grades) — not natively detected by
  Presidio out of the box.

This module is **import-cheap** (no Presidio import) so it can be used
from both the control plane (Django) and the gateway (FastAPI) without
pulling spaCy into the control image.
"""

from __future__ import annotations

from typing import Iterable


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


# Microsoft Presidio default recognizers (English).
# Source: https://microsoft.github.io/presidio/supported_entities/
# Mapping is intentionally conservative — when an entity is ambiguous
# (e.g. ``PHONE_NUMBER`` could be PII or PHI), we tag it with the broadest
# applicable framework (GDPR-PII) and let downstream policy reclassify
# based on context (e.g. caller's vertical).
PRESIDIO_ENTITY_TO_TAGS: dict[str, tuple[str, ...]] = {
    # — Pure PII —
    "PERSON":          ("GDPR-PII",),
    "EMAIL_ADDRESS":   ("GDPR-PII",),
    "PHONE_NUMBER":    ("GDPR-PII",),
    "LOCATION":        ("GDPR-PII",),
    "DATE_TIME":       (),  # too generic to tag by default
    "NRP":             ("GDPR-PII",),  # nationality / religious / political
    "IP_ADDRESS":      ("GDPR-PII",),
    "URL":             (),
    "IBAN_CODE":       ("PCI-CARD",),
    # — US-specific identifiers (mix of PII and PHI) —
    "US_SSN":          ("GDPR-PII", "HIPAA-PHI"),
    "US_DRIVER_LICENSE": ("GDPR-PII",),
    "US_PASSPORT":     ("GDPR-PII",),
    "US_ITIN":         ("GDPR-PII",),
    "US_BANK_NUMBER":  ("PCI-CARD",),
    # — Cardholder data —
    "CREDIT_CARD":     ("PCI-CARD",),
    # — Health-related —
    "MEDICAL_LICENSE": ("HIPAA-PHI",),
    # — Crypto / secrets-adjacent —
    "CRYPTO":          ("SOC2-CONF",),
    # — Generic ID-like —
    "UK_NHS":          ("HIPAA-PHI",),
    "AU_TFN":          ("GDPR-PII",),
    "AU_ACN":          ("SOC2-CONF",),
    "AU_ABN":          ("SOC2-CONF",),
    "AU_MEDICARE":     ("HIPAA-PHI",),
    "ES_NIF":          ("GDPR-PII",),
    "IT_FISCAL_CODE":  ("GDPR-PII",),
    "IT_DRIVER_LICENSE": ("GDPR-PII",),
    "IT_VAT_CODE":     ("SOC2-CONF",),
    "IT_PASSPORT":     ("GDPR-PII",),
    "IT_IDENTITY_CARD": ("GDPR-PII",),
    "SG_NRIC_FIN":     ("GDPR-PII",),
    "IN_PAN":          ("GDPR-PII",),
    "IN_AADHAAR":      ("GDPR-PII",),
    "IN_VEHICLE_REGISTRATION": ("GDPR-PII",),
    "IN_VOTER":        ("GDPR-PII",),
    "IN_PASSPORT":     ("GDPR-PII",),
}


def tags_for_entities(entity_types: Iterable[str]) -> list[str]:
    """Return sorted, deduplicated compliance tags for the given Presidio
    entity types.

    Unknown entity types are silently ignored (so adding a custom
    recognizer in Presidio without updating this map degrades to "no tag"
    rather than raising).
    """
    out: set[str] = set()
    for ent in entity_types:
        for tag in PRESIDIO_ENTITY_TO_TAGS.get(ent, ()):
            out.add(tag)
    return sorted(out)

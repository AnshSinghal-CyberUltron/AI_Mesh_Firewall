"""
Comprehensive PII / PHI / PCI / secrets policy rule catalog (50+ matchers).

Patterns align with Microsoft Presidio entity families, NIST SP 800-122 PII
categories, GDPR Art. 4 identifiers, and the in-repo ``pii_detector`` engine.
Used by ``seed_pii_policy_package`` to attach one org-scoped policy with all rules.
"""

from __future__ import annotations

import re
from typing import Any

PACKAGE_ID = "pii_comprehensive_v1"
PACKAGE_VERSION = "1.0.0"
MIN_RULE_COUNT = 50

# (entity_key, display_name, regex, replacement, action, frameworks)
# action: redact for PII; block for credential material that must not flow through LLMs
_RULE_SPECS: list[tuple[str, str, str, str, str, tuple[str, ...]]] = [
    # --- Contact & identity (GDPR / CCPA) ---
    ("EMAIL_ADDRESS", "Email address", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", "redact", ("GDPR", "CCPA")),
    ("EMAIL_PLUS_ALIAS", "Email with plus-tag", r"\b[A-Za-z0-9._%+\-]+\+[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "[REDACTED_EMAIL]", "redact", ("GDPR",)),
    ("PHONE_US", "US phone number", r"\b(?:\+1[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b", "[REDACTED_PHONE]", "redact", ("CCPA",)),
    ("PHONE_INTERNATIONAL", "International phone (E.164-style)", r"\b\+[1-9]\d{7,14}\b", "[REDACTED_PHONE]", "redact", ("GDPR",)),
    ("PHONE_IN", "India mobile number", r"\b(?:\+91[\s\-]?)?[6-9]\d{9}\b", "[REDACTED_PHONE]", "redact", ("GDPR",)),
    ("PHONE_UK", "UK phone number", r"\b(?:\+44|0)\s?(?:\d\s?){9,10}\b", "[REDACTED_PHONE]", "redact", ("GDPR",)),
    ("PHONE_GENERIC", "Generic phone (10+ digits)", r"\b(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?)\d{3,4}[\s.\-]?\d{4,6}\b", "[REDACTED_PHONE]", "redact", ("GDPR", "CCPA")),
    ("PERSON_WITH_TITLE", "Person name with honorific", r"\b(?:Mr\.|Mrs\.|Ms\.|Miss|Dr\.|Prof\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", "[REDACTED_NAME]", "redact", ("GDPR",)),
    ("DATE_OF_BIRTH", "Date of birth (labeled)", r"\b(?:DOB|D\.O\.B\.|date\s+of\s+birth|born\s+on)[:\s=]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b", "[REDACTED_DOB]", "redact", ("GDPR", "HIPAA")),
    ("STREET_ADDRESS", "US-style street address", r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Way|Court|Ct)\.?\b", "[REDACTED_ADDRESS]", "redact", ("GDPR", "CCPA")),
    ("US_ZIP", "US ZIP code", r"\b\d{5}(?:-\d{4})?\b", "[REDACTED_ZIP]", "redact", ("CCPA",)),
    ("GEO_COORDINATES", "Latitude/longitude pair", r"\b-?\d{1,3}\.\d{4,},\s*-?\d{1,3}\.\d{4,}\b", "[REDACTED_GEO]", "redact", ("GDPR",)),
    # --- US government IDs ---
    ("US_SSN", "US Social Security Number", r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b", "[REDACTED_SSN]", "redact", ("CCPA", "NIST")),
    ("US_SSN_LABELLED", "US SSN (labeled)", r"\b(?:SSN|social\s+security)[#:\s]*\d{3}[-\s]?\d{2}[-\s]?\d{4}\b", "[REDACTED_SSN]", "redact", ("CCPA", "NIST")),
    ("US_PASSPORT", "US passport number (labeled)", r"\b(?:passport)[#:\s]*[A-Z0-9]{6,9}\b", "[REDACTED_PASSPORT]", "redact", ("NIST",)),
    ("US_DRIVER_LICENSE", "US driver license (labeled)", r"\b(?:DL|driver'?s?\s*(?:license|lic))[#:\s]*[A-Z0-9]{5,12}\b", "[REDACTED_DL]", "redact", ("CCPA",)),
    ("US_EIN", "US Employer Identification Number", r"\b\d{2}-\d{7}\b", "[REDACTED_EIN]", "redact", ("NIST",)),
    ("MEDICARE_MBI", "Medicare Beneficiary Identifier", r"\b[1-9][A-HJ-NP-Z0-9]{10}\b", "[REDACTED_MBI]", "redact", ("HIPAA",)),
    # --- UK / EU ---
    ("UK_NINO", "UK National Insurance Number", r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b", "[REDACTED_NINO]", "redact", ("GDPR",)),
    ("UK_NHS", "UK NHS number", r"\b\d{3}\s?\d{3}\s?\d{4}\b", "[REDACTED_NHS]", "redact", ("GDPR", "HIPAA")),
    ("EU_VAT", "EU VAT number", r"\b[A-Z]{2}\d{8,12}\b", "[REDACTED_VAT]", "redact", ("GDPR",)),
    ("IBAN", "IBAN", r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", "[REDACTED_IBAN]", "redact", ("GDPR", "PCI")),
    ("SWIFT_BIC", "SWIFT/BIC code", r"\b[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", "[REDACTED_BIC]", "redact", ("PCI",)),
    ("CA_SIN", "Canada Social Insurance Number", r"\b\d{3}[-\s]?\d{3}[-\s]?\d{3}\b", "[REDACTED_SIN]", "redact", ("GDPR",)),
    ("AU_TFN", "Australia Tax File Number", r"\b\d{3}\s?\d{3}\s?\d{3}\b", "[REDACTED_TFN]", "redact", ("GDPR",)),
    ("DE_STEUER_ID", "Germany tax ID (11 digits)", r"\b\d{11}\b", "[REDACTED_TAX_ID]", "redact", ("GDPR",)),
    ("ES_DNI_NIE", "Spain DNI/NIE", r"\b[XYZ]?\d{7,8}[A-Z]\b", "[REDACTED_DNI]", "redact", ("GDPR",)),
    ("IT_CODICE_FISCALE", "Italy Codice Fiscale", r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", "[REDACTED_CF]", "redact", ("GDPR",)),
    # --- India ---
    ("AADHAAR", "India Aadhaar (12 digits)", r"\b\d{4}\s?\d{4}\s?\d{4}\b(?!\s?\d{4})", "[REDACTED_AADHAAR]", "redact", ("GDPR",)),
    ("IN_PAN", "India PAN", r"\b[A-Z]{5}\d{4}[A-Z]\b", "[REDACTED_PAN]", "redact", ("GDPR",)),
    ("IN_VOTER_ID", "India Voter ID", r"\b[A-Z]{3}\d{7}\b", "[REDACTED_VOTER]", "redact", ("GDPR",)),
    ("IN_DRIVING_LICENSE", "India driving license", r"\b[A-Z]{2}[\-\s]?\d{2}\s?\d{4}\s?\d{7}\b", "[REDACTED_DL]", "redact", ("GDPR",)),
    ("IN_PASSPORT", "India passport (labeled)", r"\b(?:passport|passport\s+no|passport\s+number)[:\s#]*[A-Z]\d{7}\b", "[REDACTED_PASSPORT]", "redact", ("GDPR",)),
    # --- PCI ---
    ("CREDIT_CARD", "Credit card (grouped)", r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b", "[REDACTED_CARD]", "redact", ("PCI-DSS",)),
    ("CREDIT_CARD_LUHN_CANDIDATE", "Credit card (13–19 digits)", r"\b(?:\d[ -]*?){13,19}\b", "[REDACTED_CARD]", "redact", ("PCI-DSS",)),
    ("CARD_CVV", "Card CVV/CVC", r"\b(?:CVV|CVC|CID)[:\s#]*\d{3,4}\b", "[REDACTED_CVV]", "redact", ("PCI-DSS",)),
    ("CARD_EXPIRY", "Card expiry (MM/YY)", r"\b(?:exp(?:iry)?|valid\s+thru)[:\s]*\d{2}[/\-]\d{2,4}\b", "[REDACTED_EXPIRY]", "redact", ("PCI-DSS",)),
    ("US_BANK_ACCOUNT", "US bank account (labeled)", r"\b(?:account|routing)\s*#?\s*[:\s]*\d{8,17}\b", "[REDACTED_BANK]", "redact", ("PCI-DSS",)),
    ("US_ROUTING_NUMBER", "US ABA routing number", r"\b\d{9}\b", "[REDACTED_ROUTING]", "redact", ("PCI-DSS",)),
    # --- PHI ---
    ("MEDICAL_NPI_DEA", "NPI / DEA number", r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b", "[REDACTED_MEDICAL_ID]", "redact", ("HIPAA",)),
    ("MEDICAL_MRN", "Medical record number", r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b", "[REDACTED_MRN]", "redact", ("HIPAA",)),
    ("INSURANCE_MEMBER_ID", "Insurance member ID", r"\b(?:insurance\s+(?:id|number|#)|member\s+id|policy\s*#)\s*[:\s]*[A-Z0-9]{5,15}\b", "[REDACTED_INSURANCE]", "redact", ("HIPAA",)),
    ("HEALTH_PLAN_ID", "Health plan beneficiary ID", r"\b(?:beneficiary|subscriber)\s*(?:id|#)[:\s]*[A-Z0-9]{6,14}\b", "[REDACTED_HEALTH_ID]", "redact", ("HIPAA",)),
    # --- Network & device ---
    ("IP_V4", "IPv4 address", r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b", "[REDACTED_IP]", "redact", ("NIST",)),
    ("IP_V6", "IPv6 address", r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b", "[REDACTED_IP]", "redact", ("NIST",)),
    ("MAC_ADDRESS", "MAC address", r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b", "[REDACTED_MAC]", "redact", ("NIST",)),
    ("IMEI", "IMEI (15 digits)", r"\b\d{15}\b", "[REDACTED_IMEI]", "redact", ("GDPR",)),
    ("VIN", "Vehicle VIN", r"\b[A-HJ-NPR-Z0-9]{17}\b", "[REDACTED_VIN]", "redact", ("CCPA",)),
    ("URL", "HTTP(S) URL", r"\bhttps?://[^\s<>\"']+\b", "[REDACTED_URL]", "redact", ("GDPR",)),
    ("URL_WITH_CREDENTIALS", "URL with embedded credentials", r"\bhttps?://[^\s:@/]+:[^\s@/]+@[^\s<>\"']+\b", "[REDACTED_URL]", "block", ("NIST", "PCI")),
    # --- Crypto ---
    ("CRYPTO_BTC", "Bitcoin address", r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b", "[REDACTED_CRYPTO]", "redact", ("GDPR",)),
    ("CRYPTO_ETH", "Ethereum address", r"\b0x[a-fA-F0-9]{40}\b", "[REDACTED_CRYPTO]", "redact", ("GDPR",)),
    ("CRYPTO_BECH32", "Bitcoin bech32 address", r"\bbc1[a-z0-9]{39,59}\b", "[REDACTED_CRYPTO]", "redact", ("GDPR",)),
    # --- Secrets & credentials (block or redact) ---
    ("OPENAI_API_KEY", "OpenAI API key", r"\bsk-[a-zA-Z0-9]{20,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("ANTHROPIC_API_KEY", "Anthropic API key", r"\bsk-ant-[a-zA-Z0-9\-]{20,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("AWS_ACCESS_KEY", "AWS access key ID", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]", "block", ("NIST",)),
    ("AWS_SECRET_KEY", "AWS secret access key (labeled)", r"\b(?:aws)?[_\s-]?secret[_\s-]?access[_\s-]?key[\"'\s:=]+[A-Za-z0-9/+=]{40}\b", "[REDACTED_AWS_SECRET]", "block", ("NIST",)),
    ("GITHUB_TOKEN", "GitHub token", r"\bgh[pousr]_[a-zA-Z0-9]{36,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("SLACK_TOKEN", "Slack token", r"\bxox[bpas]-[0-9]{10,}-[a-zA-Z0-9\-]+\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("GCP_API_KEY", "Google API key", r"\bAIza[0-9A-Za-z\-_]{35}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("STRIPE_KEY", "Stripe secret key", r"\b(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{20,}\b", "[REDACTED_SECRET]", "block", ("PCI", "NIST")),
    ("JWT", "JSON Web Token", r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b", "[REDACTED_JWT]", "block", ("NIST",)),
    ("BEARER_TOKEN", "Bearer / generic token", r"\b(?:Bearer|token)\s+[A-Za-z0-9_\-\.]{20,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("GENERIC_API_KEY", "Generic API key assignment", r"(?:api[_\-]?key|apikey)[\"\s:=]+[A-Za-z0-9_\-\.]{16,}", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("API_KEY_PREFIXED", "Prefixed API tokens (sk/pk/rk/ghp)", r"\b(?:sk|pk|rk|api|key|tok|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b", "[REDACTED_SECRET]", "block", ("NIST",)),
    ("AZURE_CONNECTION_STRING", "Azure storage connection string", r"\bDefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]{40,}", "[REDACTED_AZURE]", "block", ("NIST",)),
    ("PRIVATE_KEY_PEM", "PEM private key header", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", "block", ("NIST",)),
    ("SSH_PRIVATE_KEY", "OpenSSH private key", r"-----BEGIN OPENSSH PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", "block", ("NIST",)),
    ("PASSWORD_IN_CONFIG", "Password in config line", r"(?:password|passwd|pwd)[\"\s:=]+[^\s\"']{8,}", "[REDACTED_PASSWORD]", "block", ("NIST",)),
    ("DATABASE_URL", "Database connection URL with password", r"\b(?:postgres|mysql|mongodb)(?:\+[a-z]+)?://[^\s:@/]+:[^\s@/]+@[^\s<>\"']+\b", "[REDACTED_DB_URL]", "block", ("NIST", "PCI")),
    ("STUDENT_ID", "Student ID (labeled)", r"\b(?:student\s+id|sid)[:\s#]*[A-Z0-9]{5,12}\b", "[REDACTED_STUDENT_ID]", "redact", ("FERPA",)),
    ("EMPLOYEE_ID", "Employee ID (labeled)", r"\b(?:employee\s+id|emp\s*id|badge\s*#)[:\s#]*[A-Z0-9]{4,12}\b", "[REDACTED_EMPLOYEE_ID]", "redact", ("GDPR",)),
]


def _validate_catalog() -> None:
    seen: set[str] = set()
    for entity_key, _name, pattern, _repl, action, _fw in _RULE_SPECS:
        if entity_key in seen:
            raise ValueError(f"duplicate entity_key in catalog: {entity_key}")
        seen.add(entity_key)
        re.compile(pattern)
        if action not in {"redact", "block", "monitor"}:
            raise ValueError(f"invalid action for {entity_key}: {action}")
    if len(_RULE_SPECS) < MIN_RULE_COUNT:
        raise ValueError(f"catalog must define at least {MIN_RULE_COUNT} rules, got {len(_RULE_SPECS)}")


_validate_catalog()


def policy_code_for_org(org_id: int) -> str:
    return f"PII_PKG_{org_id}"


def build_rule_dicts() -> list[dict[str, Any]]:
    """Return rule payloads suitable for Rule bulk_create / JSON fixtures."""
    rules: list[dict[str, Any]] = []
    total = len(_RULE_SPECS)
    for idx, (entity_key, label, pattern, replacement, action, frameworks) in enumerate(_RULE_SPECS):
        rules.append(
            {
                "name": label,
                "rule_type": "regex",
                "condition": {
                    "regex": pattern,
                    "field": "both",
                    "entity_key": entity_key,
                    "package_id": PACKAGE_ID,
                },
                "action": action,
                "redaction_config": {"replacement": replacement},
                "priority": total - idx,
                "enabled": True,
                "description": (
                    f"PII package rule ({entity_key}). Frameworks: {', '.join(frameworks)}."
                ),
            }
        )
    return rules


def package_metadata() -> dict[str, Any]:
    return {
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "rule_count": len(_RULE_SPECS),
        "standards": ["GDPR", "CCPA", "HIPAA", "PCI-DSS", "NIST-PII", "FERPA"],
        "source": "policy.pii_policy_catalog",
    }

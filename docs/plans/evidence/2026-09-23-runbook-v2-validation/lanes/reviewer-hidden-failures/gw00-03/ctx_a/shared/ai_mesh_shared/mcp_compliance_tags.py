"""Entity / preset key → compliance tag mapping for MCP scan findings.

All tags in this module use the ComplianceTag CATALOG vocabulary
(``GDPR-PII`` / ``HIPAA-PHI`` / ``PCI-CARD`` / ``SOC2-CONF`` / ``ITAR`` /
``FERPA``) — the same codes seeded into the ``ComplianceTag`` Django model and
documented as the contract for ``MCPEvent.compliance_tags`` (a "sorted list of
ComplianceTag.code values"). Keep the catalog set below in sync with
``control/ai_mesh_control/policy/compliance_tags.py``.
"""

PRESET_TO_TAGS: dict[str, tuple[str, ...]] = {
    "credit_card": ("PCI-CARD",),
    "us_ssn": ("GDPR-PII", "HIPAA-PHI"),
    "email": ("GDPR-PII",),
    "phone": ("GDPR-PII",),
    "ip_address": ("GDPR-PII",),
    "api_key": ("SOC2-CONF",),
    "aws_access_key": ("SOC2-CONF",),
    "iban": ("PCI-CARD",),
    "jwt": ("SOC2-CONF",),
    "private_key": ("SOC2-CONF",),
    "pii": ("GDPR-PII",),
    "secret": ("SOC2-CONF",),
    "policy_match": ("GDPR-PII",),
    # CHG-0059: internal-infrastructure leakage keys (the gateway's
    # ``detect_ip_leakage`` / IP_LEAKAGE_PATTERNS names). Internal IPs, hostnames,
    # URLs, and private file paths are confidential internal identifiers -> the
    # catalog's SOC2-CONF bucket ("Secrets, tokens, internal credentials, and
    # confidential identifiers"). Distinct from the generic public ``ip_address``
    # preset above, which is a personal-data identifier (GDPR-PII).
    "internal_ipv4": ("SOC2-CONF",),
    "internal_hostname": ("SOC2-CONF",),
    "internal_url": ("SOC2-CONF",),
    "file_path_unix": ("SOC2-CONF",),
    "file_path_windows": ("SOC2-CONF",),
    "ip_leakage": ("SOC2-CONF",),
}


def tags_for_preset_or_entity(key: str) -> list[str]:
    if not key:
        return []
    k = key.strip().lower()
    tags = PRESET_TO_TAGS.get(k) or PRESET_TO_TAGS.get(key) or ()
    return sorted(set(tags))


# --- CHG-0059: gateway-granular -> catalog-code normalization ----------------
# The gateway scan path (gateway/ai_mesh_gateway/patterns.py COMPLIANCE_TAG_MAP)
# emits a GRANULAR regulation/data-type vocabulary (GDPR, HIPAA, PII, PHI,
# PCI-DSS, SECRET, INFRA, SOC2), whereas the ComplianceTag catalog + this module
# use the FUSED catalog codes (GDPR-PII, HIPAA-PHI, PCI-CARD, SOC2-CONF, ...).
# MCPEvent.compliance_tags is documented as a list of ComplianceTag.code values,
# so gateway-recorded events must be normalized onto the catalog vocabulary to
# keep the audit field consistent across recording planes (control-plane
# enforcement events already use catalog codes via tags_for_preset_or_entity).

# Authoritative catalog code set — keep in sync with
# control/ai_mesh_control/policy/compliance_tags.py COMPLIANCE_TAG_CODES.
CATALOG_TAG_CODES: frozenset = frozenset(
    {"GDPR-PII", "HIPAA-PHI", "PCI-CARD", "SOC2-CONF", "ITAR", "FERPA"}
)

# Granular gateway token (upper-cased) -> catalog code.
_GATEWAY_TAG_TO_CATALOG: dict[str, str] = {
    "GDPR": "GDPR-PII",
    "PII": "GDPR-PII",
    "HIPAA": "HIPAA-PHI",
    "PHI": "HIPAA-PHI",
    "PCI-DSS": "PCI-CARD",
    "PCI": "PCI-CARD",
    "SECRET": "SOC2-CONF",
    "INFRA": "SOC2-CONF",
    "SOC2": "SOC2-CONF",
}


def to_catalog_codes(tags) -> list[str]:
    """Normalize a mix of gateway-granular and/or catalog compliance tags onto the
    ComplianceTag catalog vocabulary. Returns a sorted, de-duplicated list.

    IDEMPOTENT: values already in the catalog vocabulary pass through unchanged, so
    re-normalizing an already-catalog list is a no-op. UNKNOWN tokens (not a known
    granular token and not a catalog code) pass through UNCHANGED — normalization
    never silently drops a tag (a compliance signal is only ever renamed onto the
    canonical vocab, never discarded). Non-string / blank entries are skipped.
    """
    if not tags:
        return []
    out: set[str] = set()
    for t in tags:
        if not isinstance(t, str):
            continue
        s = t.strip()
        if not s:
            continue
        mapped = _GATEWAY_TAG_TO_CATALOG.get(s) or _GATEWAY_TAG_TO_CATALOG.get(s.upper())
        out.add(mapped if mapped else s)
    return sorted(out)

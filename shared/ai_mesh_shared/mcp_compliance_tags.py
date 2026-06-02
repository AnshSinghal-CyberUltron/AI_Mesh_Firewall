"""Entity / preset key → compliance tag mapping for MCP scan findings."""

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
}


def tags_for_preset_or_entity(key: str) -> list[str]:
    if not key:
        return []
    k = key.strip().lower()
    tags = PRESET_TO_TAGS.get(k) or PRESET_TO_TAGS.get(key) or ()
    return sorted(set(tags))

"""
Compliance service: derive ComplianceViolation rows from EnforcementEvents.

Mapping logic:
  pii_detected.category == "PII"   -> GDPR
  pii_detected.category == "PHI"   -> GDPR + HIPAA
  pii_detected.category == "PCI"   -> PCIDSS
  pii_detected.category == "SECRET" -> SOC2 + GDPR (credentials / secrets)
  owasp_code == "LLM06" (no PII)   -> GDPR (sensitive info disclosure)
  owasp_code == "MCP06"            -> GDPR (data exfiltration via tools)
  owasp_code == "MCP02"            -> SOC2 (unauthorized API / access control)
  ev.action == "block"             -> SOC2 (access control)
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# Maps PIIResult.category to (framework, violation_type, severity) tuples
_PII_FRAMEWORK_MAP: dict[str, list[tuple[str, str, str]]] = {
    "PII": [("GDPR", "data_leakage", "high")],
    "PHI": [
        ("GDPR", "data_leakage", "high"),
        ("HIPAA", "phi_protection", "critical"),
    ],
    "PCI": [("PCIDSS", "data_leakage", "high")],
    "SECRET": [
        ("SOC2", "credential_exposure", "critical"),
        ("GDPR", "data_leakage", "high"),
    ],
}


def create_violations_for_event(ev) -> None:
    """
    Inspect EnforcementEvent metadata and create ComplianceViolation rows
    for every applicable framework.  Safe to call in a try/except — any
    failure is logged but does not affect the enforcement response.
    """
    # Late import avoids circular dependency during app startup
    from policy.models import ComplianceViolation

    meta = ev.metadata or {}
    pii = meta.get("pii_detected") or {}
    owasp_codes = meta.get("owasp_codes")
    if owasp_codes is not None:
        owasp_codes = [str(c).strip().upper() for c in owasp_codes if c]
    else:
        single = (meta.get("owasp_code") or "").strip().upper()
        owasp_codes = [single] if single else []
    pii_detected = bool(pii.get("detected"))
    pii_category = pii.get("category", "")

    rows: list[ComplianceViolation] = []

    # ── PII / PHI / PCI → framework violations ───────────────────────────
    if pii_detected:
        for framework, vtype, sev in _PII_FRAMEWORK_MAP.get(pii_category, []):
            rows.append(
                ComplianceViolation(
                    enforcement_event=ev,
                    framework=framework,
                    violation_type=vtype,
                    severity=sev,
                    description=(
                        f"{framework} violation: {pii_category} data detected "
                        f"(severity: {pii.get('severity', 'unknown')})"
                    ),
                )
            )

    # ── LLM06 Sensitive Information Disclosure (no PII entity match) ─────
    if "LLM06" in owasp_codes and not pii_detected:
        rows.append(
            ComplianceViolation(
                enforcement_event=ev,
                framework="GDPR",
                violation_type="sensitive_info_disclosure",
                severity="high",
                description="GDPR: Sensitive information disclosure detected (OWASP LLM06)",
            )
        )

    # ── MCP06 Data Exfiltration via Tools → GDPR ──────────────────────────
    if "MCP06" in owasp_codes:
        rows.append(
            ComplianceViolation(
                enforcement_event=ev,
                framework="GDPR",
                violation_type="data_exfiltration",
                severity="high",
                description="GDPR: Data exfiltration via tools detected (OWASP MCP06)",
            )
        )

    # ── MCP02 Unauthorized API Calls → SOC2 ───────────────────────────────
    if "MCP02" in owasp_codes:
        rows.append(
            ComplianceViolation(
                enforcement_event=ev,
                framework="SOC2",
                violation_type="unauthorized_access",
                severity="medium",
                description="SOC2: Unauthorized API calls detected (OWASP MCP02)",
            )
        )

    # ── AGENTIC06 State Manipulation → SOC2 ─────────────────────────────────
    if "AGENTIC06" in owasp_codes:
        rows.append(
            ComplianceViolation(
                enforcement_event=ev,
                framework="SOC2",
                violation_type="integrity_violation",
                severity="high",
                description="SOC2: Agent state manipulation detected (OWASP AGENTIC06)",
            )
        )

    # ── AGENTIC09 Memory Poisoning → GDPR ───────────────────────────────────
    if "AGENTIC09" in owasp_codes:
        rows.append(
            ComplianceViolation(
                enforcement_event=ev,
                framework="GDPR",
                violation_type="data_integrity",
                severity="high",
                description="GDPR: Memory/context poisoning detected (OWASP AGENTIC09)",
            )
        )

    # ── Any block action → SOC2 access-control event ─────────────────────
    if ev.action == "block":
        rows.append(
            ComplianceViolation(
                enforcement_event=ev,
                framework="SOC2",
                violation_type="access_control",
                severity="medium",
                description="SOC2: Access control — request blocked by policy/security engine",
            )
        )

    if rows:
        ComplianceViolation.objects.bulk_create(rows)
        logger.debug(
            "Created %d compliance violation(s) for EnforcementEvent %s",
            len(rows),
            ev.id,
        )


def resolve_violations_for_event(ev) -> None:
    """
    Mark all open ComplianceViolation rows linked to this EnforcementEvent
    as resolved.  Called when the incident is resolved via the SOC UI.
    """
    from policy.models import ComplianceViolation

    updated = ComplianceViolation.objects.filter(enforcement_event=ev, status="open").update(
        status="resolved", resolved_at=timezone.now()
    )

    if updated:
        logger.debug(
            "Resolved %d compliance violation(s) for EnforcementEvent %s",
            updated,
            ev.id,
        )

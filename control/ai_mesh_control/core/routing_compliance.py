"""Canonical compliance-framework identity for routing.

Compliance tags are matched by the gateway's routing hard filter
(``llm_router._score_routing_models``). They arrive from two uncoordinated directions:

  * operators typing them into the model form ("hipaa", "HIPAA", "PCI-DSS", "pci_dss");
  * API clients sending ``routing_preferences.compliance_requirements``.

The gateway originally compared them with an exact ``in`` test, so casing or separator
drift between those two sources read as "no model satisfies this tag" and produced a
spurious 403 ``compliance_routing_unsatisfiable``. Both sides now canonicalize first.

This module is the control-plane half; it MUST stay in step with
``gateway/ai_mesh_gateway/llm_router.py::canonical_compliance_tag``.
"""
from __future__ import annotations

import re

# Canonical form: upper-case, runs of non-alphanumerics collapsed to "_".
_CANON_RE = re.compile(r"[^A-Z0-9]+")

# Frameworks an operator may attach to a model. Canonical spellings.
SUPPORTED_COMPLIANCE_FRAMEWORKS: tuple[str, ...] = (
    "SOC2",
    "ISO27001",
    "HIPAA",
    "GDPR",
    "PCI_DSS",
    "NIST",
)

# Variants folded onto one identity. Keys are already canonicalized.
_ALIASES = {
    "SOC_2": "SOC2",
    "SOC2_TYPE_II": "SOC2",
    "SOC2_TYPE2": "SOC2",
    "ISO_27001": "ISO27001",
    "ISO_IEC_27001": "ISO27001",
    "PCIDSS": "PCI_DSS",
    "PCI": "PCI_DSS",
    "HIPPA": "HIPAA",          # common misspelling, seen in operator input
    "NIST_CSF": "NIST",
    "NIST_800_53": "NIST",
    "GDPR_EU": "GDPR",
}

# Human-facing labels for the console.
COMPLIANCE_FRAMEWORK_CHOICES: tuple[tuple[str, str], ...] = (
    ("SOC2", "SOC 2"),
    ("ISO27001", "ISO 27001"),
    ("HIPAA", "HIPAA"),
    ("GDPR", "GDPR"),
    ("PCI_DSS", "PCI-DSS"),
    ("NIST", "NIST"),
)


def canonical_compliance_tag(value: object) -> str:
    """Normalize one compliance token to its canonical identity ('' when empty)."""
    token = _CANON_RE.sub("_", str(value or "").strip().upper()).strip("_")
    if not token:
        return ""
    return _ALIASES.get(token, token)


def canonical_compliance_list(values: object) -> list[str]:
    """Canonicalize an iterable of tags, de-duplicated, order preserved."""
    if values in (None, ""):
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    out: list[str] = []
    try:
        for item in values:  # type: ignore[union-attr]
            tag = canonical_compliance_tag(item)
            if tag and tag not in out:
                out.append(tag)
    except TypeError:
        tag = canonical_compliance_tag(values)
        if tag:
            out.append(tag)
    return out

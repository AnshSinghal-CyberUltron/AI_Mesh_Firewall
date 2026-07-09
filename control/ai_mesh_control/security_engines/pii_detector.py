"""
PII/PHI/PCI Detector using regex patterns.

Lightweight regex-based detection of structured sensitive data (SSN, credit
cards, emails, etc.). Semantic PII (person names in unstructured text,
locations) is deferred to Bedrock Tier-2 scanning.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

LOG = logging.getLogger("security_engines.pii_detector")

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "PERSON": re.compile(r"\b(?:Mr\.|Mrs\.|Ms\.|Dr\.|Prof\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b"),
    "EMAIL_ADDRESS": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    # Indian mobile before US PHONE_NUMBER so 10-digit IN numbers are not misclassified as US.
    "IN_PHONE_NUMBER": re.compile(r"\b(?:\+91[\s\-]?)?[6-9]\d{9}\b"),
    "PHONE_NUMBER": re.compile(r"\b(?:\+1[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b"),
    "US_SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # 12-digit Aadhaar; reject both a trailing 4th group (16-digit card suffix) AND
    # a PRECEDING 4-digit group, so the trailing 12 digits of a 16-digit credit card
    # never sub-match as Aadhaar (mirrors the policy-catalog AADHAAR lookbehind fix).
    "AADHAAR_NUMBER": re.compile(r"(?<!\d)(?<!\d\s)\b\d{4}\s?\d{4}\s?\d{4}\b(?!\s?\d{4})"),
    "IN_PAN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE),
    "IN_VOTER_ID": re.compile(r"\b[A-Z]{3}\d{7}\b"),
    "IN_DRIVING_LICENSE": re.compile(
        r"\b[A-Z]{2}[\-\s]?\d{2}\s?\d{4}\s?\d{7}\b"
    ),
    "IN_PASSPORT": re.compile(
        r"\b(?:passport|passport\s+no|passport\s+number)[:\s#]*[A-Z]\d{7}\b",
        re.IGNORECASE,
    ),
    "US_DRIVER_LICENSE": re.compile(
        r"\b(?:DL|driver'?s?\s*(?:license|lic))[#:\s]*[A-Z0-9]{5,12}\b",
        re.IGNORECASE,
    ),
    "DATE_OF_BIRTH": re.compile(
        r"\b(?:DOB|D\.O\.B\.|date\s+of\s+birth)[:\s=]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
        re.IGNORECASE,
    ),
    "US_PASSPORT": re.compile(r"\b(?:passport)[#:\s]*[A-Z0-9]{6,9}\b", re.IGNORECASE),
    "LOCATION": re.compile(
        r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+"
        r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Way|Court|Ct)"
        r"\.?\b"
    ),
}

PHI_PATTERNS: dict[str, re.Pattern[str]] = {
    "MEDICAL_LICENSE": re.compile(r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b", re.IGNORECASE),
    "MEDICAL_RECORD": re.compile(r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b", re.IGNORECASE),
    "INSURANCE_ID": re.compile(
        r"\b(?:insurance\s+(?:id|number|#)|policy\s*#)\s*[:\s]*[A-Z0-9]{5,15}\b",
        re.IGNORECASE,
    ),
}

PCI_PATTERNS: dict[str, re.Pattern[str]] = {
    "CREDIT_CARD": re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
    "IBAN_CODE": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b"),
    "US_BANK_NUMBER": re.compile(r"\b(?:account|routing)\s*#?\s*[:\s]*\d{8,17}\b", re.IGNORECASE),
    "CRYPTO": re.compile(r"\b(?:0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})\b"),
}

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "OPENAI_API_KEY": re.compile(r"\bsk-[a-zA-Z0-9]{32,}\b"),
    "AWS_ACCESS_KEY": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GITHUB_TOKEN": re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{36,}\b"),
    "SLACK_TOKEN": re.compile(r"\bxox[bpas]-[0-9]{10,}-[a-zA-Z0-9\-]+\b"),
    "GENERIC_BEARER_TOKEN": re.compile(
        r"\b(?:Bearer|token)\s+[A-Za-z0-9_\-\.]{20,}\b",
        re.IGNORECASE,
    ),
    "PRIVATE_KEY": re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
    "GENERIC_API_KEY": re.compile(
        r"(?:api[_\-]?key|apikey)[\"\s:=]+[A-Za-z0-9_\-\.]{16,}",
        re.IGNORECASE,
    ),
}


@dataclass
class PIIResult:
    detected: bool
    category: str
    entities: dict[str, list[dict[str, Any]]]
    severity: str
    anonymized_text: str


class PIIDetector:
    """
    Regex-based PII/PHI/PCI detection.

    Exposes a stable PIIResult interface so callers (integrated_scanner,
    risk_scorer) require zero changes.
    """

    def __init__(self) -> None:
        self.categories: dict[str, list[str]] = {
            "PII": list(PII_PATTERNS.keys()),
            "PHI": list(PHI_PATTERNS.keys()),
            "PCI": list(PCI_PATTERNS.keys()),
            "SECRET": list(SECRET_PATTERNS.keys()),
        }

    def detect(self, text: str, context: str = "general") -> PIIResult:
        """
        Detect PII/PHI/PCI in text.

        Args:
            text: Text to scan.
            context: "general", "medical", or "financial" -- influences PHI sensitivity.

        Returns:
            PIIResult with detection details.
        """
        found_entities: dict[str, list[dict[str, Any]]] = {
            "PII": [],
            "PHI": [],
            "PCI": [],
            "SECRET": [],
        }

        for entity_type, pattern in PCI_PATTERNS.items():
            for match in pattern.finditer(text):
                found_entities["PCI"].append(self._match_to_entity(entity_type, match, text))

        for entity_type, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                found_entities["SECRET"].append(self._match_to_entity(entity_type, match, text))

        # Normalize context: the caller passes it straight from the request body
        # (evaluation_views body.get("context", "general")), so an exact-match
        # `context == "medical"` silently skipped ALL PHI (critical) detection for
        # "Medical" / "MEDICAL" / " medical". Case- and whitespace-insensitive now.
        if (context or "").strip().lower() == "medical":
            for entity_type, pattern in PHI_PATTERNS.items():
                for match in pattern.finditer(text):
                    found_entities["PHI"].append(self._match_to_entity(entity_type, match, text))

        for entity_type, pattern in PII_PATTERNS.items():
            for match in pattern.finditer(text):
                found_entities["PII"].append(self._match_to_entity(entity_type, match, text))

        has_pci = bool(found_entities["PCI"])
        has_secret = bool(found_entities["SECRET"])
        has_phi = bool(found_entities["PHI"])
        has_pii = bool(found_entities["PII"])
        detected = has_pci or has_secret or has_phi or has_pii

        if has_pci:
            severity = "critical"
            category = "PCI"
        elif has_secret:
            severity = "critical"
            category = "SECRET"
        elif has_phi:
            severity = "critical"
            category = "PHI"
        elif has_pii:
            severity = "high"
            category = "PII"
        else:
            severity = "none"
            category = "none"

        anonymized_text = self._anonymize(text, found_entities) if detected else text

        return PIIResult(
            detected=detected,
            category=category,
            entities=found_entities,
            severity=severity,
            anonymized_text=anonymized_text,
        )

    def bulk_scan(self, texts: list[str]) -> list[PIIResult]:
        """Scan multiple texts."""
        return [self.detect(text) for text in texts]

    @staticmethod
    def _match_to_entity(entity_type: str, match: re.Match[str], text: str) -> dict[str, Any]:
        return {
            "type": entity_type,
            "score": 1.0,
            "start": match.start(),
            "end": match.end(),
            "text": text[match.start() : match.end()],
        }

    @staticmethod
    def _anonymize(text: str, found_entities: dict[str, list[dict[str, Any]]]) -> str:
        # #38: the old algorithm spliced right-to-left but only de-duped EXACT
        # spans. For PARTIALLY-OVERLAPPING spans (two detectors matching the same
        # digit run, e.g. SSN vs CREDIT_CARD) the right-to-left splice applied
        # original indices to an already-modified string, corrupting output and
        # LEAKING RAW PII DIGITS (e.g. "<SSN>ARD>56789XY"). Fix: MERGE overlapping
        # spans into disjoint union intervals first, so every covered byte is
        # redacted and the right-to-left splice only ever touches non-overlapping
        # intervals (index-safe).
        all_matches: list[dict[str, Any]] = []
        for entities in found_entities.values():
            all_matches.extend(entities)
        if not all_matches:
            return text

        # Sort by start asc, then longer span first, to build union intervals.
        all_matches.sort(key=lambda m: (m["start"], -m["end"]))
        merged: list[list[Any]] = []  # [start, end, type_label]
        for m in all_matches:
            s, e, t = m["start"], m["end"], m["type"]
            if merged and s < merged[-1][1]:  # overlaps the previous union
                if e > merged[-1][1]:
                    merged[-1][1] = e  # extend to cover the union
                # keep the first (leftmost) entity's type label for the merge
            else:
                merged.append([s, e, t])

        # Apply right-to-left over now-disjoint intervals (index-safe).
        result = text
        for s, e, t in sorted(merged, key=lambda x: x[0], reverse=True):
            result = result[:s] + f"<{t}>" + result[e:]
        return result


if __name__ == "__main__":
    detector = PIIDetector()
    test_cases = [
        ("My email is john.doe@example.com and phone is 555-123-4567", "general"),
        ("Patient Mr. John Smith, DOB: 01/15/1980, SSN 123-45-6789", "medical"),
        ("Credit card: 4532-1234-5678-9010, expires 12/25", "financial"),
        ("The weather is nice today", "general"),
    ]
    for text, ctx in test_cases:
        r = detector.detect(text, ctx)
        print(f"[{ctx}] detected={r.detected} cat={r.category} sev={r.severity}")
        if r.detected:
            print(f"  anonymized: {r.anonymized_text}")

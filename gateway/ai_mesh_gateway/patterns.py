"""
Shared regex patterns for PII, secrets, credentials, output guardrails.

Single source of truth for the gateway data plane. Both ``scanner.py``
and ``context_guard.py`` import from this module to eliminate duplication.

Provides:
- Cached regex compilation via ``compile_pattern()``
- Detection helpers: ``detect_pii()``, ``detect_secrets()``
- Output guardrail patterns: hallucination, IP leakage, credential exposure
- Redaction helper: ``redact_all()``
- Compliance tagging: ``COMPLIANCE_TAG_MAP``, ``get_compliance_tags()``
"""
from __future__ import annotations

import base64
import functools
import re
import unicodedata
import urllib.parse
from typing import Dict, List


@functools.lru_cache(maxsize=512)
def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile and cache regex patterns for performance."""
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Obfuscation-resistant canonicalization (ATTACK_LANDSCAPE G1/G2).
#
# detect_pii/detect_secrets/redact_all historically matched RAW text only, so PII
# and secrets hidden with zero-width, fullwidth, unicode-dash, homoglyph, combining
# marks, or base64/hex encoding evaded BOTH detection and masking and egressed in
# cleartext. ``_canonicalize_with_map`` folds an obfuscated string to a canonical
# form using ONLY position-preserving 1->1 substitutions and 1->0 removals, and
# returns an index map so a match found in the canonical form is masked back in the
# ORIGINAL bytes (the only bytes that egress). It is a NO-OP on plain ASCII, so the
# frozen golden cases and existing redaction behaviour are unchanged. Every step is a
# single linear char scan under a hard length cap => ReDoS/DoS-safe.
# ---------------------------------------------------------------------------

_CANON_MAX_LEN = 20000  # hard cap (upstream MAX_PROMPT_LENGTH is 10k)

# Unicode dashes NFKC does NOT fold to ASCII '-' (e.g. U+2011 non-breaking hyphen).
_DASH_CHARS = frozenset("‐‑‒–—―−﹘﹣－")

# Cross-script confusables NFKC leaves untouched (Cyrillic/Greek -> Latin skeleton).
# G56: a SINGLE homoglyph substituted into a PII/secret/credential value (``sk_live_abcԁ…``
# with Cyrillic ԁ, ``exampӏe.com`` with palochka ӏ) breaks the raw regex AND was not folded
# here, so it evaded detection AND masking on both input and output. The lowercase Cyrillic +
# Greek caps below were the original set; the rest complete the standard Unicode Latin-lookalike
# confusable set (Cyrillic lowercase ԁ/һ/ӏ/ԛ/ԝ, the classic Cyrillic UPPERCASE homoglyphs
# А/В/Е/К/М/Н/О/Р/С/Т/У/Х/Ѕ/Ј/І…, Greek ρ/κ/τ and the lunate sigma). NFKC runs BEFORE this map
# (line below), so Greek lunate ϲ (U+03F2) folds to final sigma ς first -> ς is what we map to c.
# Only consulted inside canonicalize_for_detection, and a match fires only when the CANONICAL
# form is a real PII/secret pattern, so legitimate Cyrillic/Greek prose is unaffected (FP-safe).
_CONFUSABLE_MAP = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s",
    # Cyrillic lowercase (NFKC-identity) not covered above
    "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q", "ԝ": "w",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "ο": "o", "α": "a", "ι": "i", "ν": "v",
    # Greek lowercase homoglyphs (post-NFKC): rho/kappa/tau look like p/k/t; final sigma ς
    # (what the lunate sigma ϲ folds to) imitates a Latin c; mu imitates u (micro-sign µ
    # NFKC-folds to μ first, so this covers both).
    "ρ": "p", "κ": "k", "τ": "t", "ς": "c", "μ": "u",
    # G95: the original Greek lowercase set omitted epsilon/eta/gamma/upsilon/chi/omega,
    # so a homoglyph injection that swaps Latin e/n/y/u/x/w for their Greek lookalikes
    # (``ignorε all prεvious instructions``, ``rεvεal thε systεm promρt``) canonicalized
    # to a NON-matching skeleton and slipped past BOTH the injection scan and
    # detect_pii/detect_secrets. epsilon is the worst offender — ``e`` saturates the
    # attack lexicon. Lunate epsilon ϵ (U+03F5) NFKC-folds to ε first, so this covers it
    # too. FP-safe: a match only fires when the CANONICAL form is a real pattern, and no
    # benign Greek prose canonicalizes to an English attack phrase / PII / secret.
    "ε": "e", "η": "n", "γ": "y", "υ": "u", "χ": "x", "ω": "w",
    # Cyrillic UPPERCASE — visually identical to Latin capitals (classic homoglyph set)
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S", "Ј": "J",
    "І": "I", "Ԛ": "Q", "Ԝ": "W", "Ԁ": "D", "Һ": "H",
}

# G21: Unicode SMALL-CAPITAL letters (ɪɢɴᴏʀᴇ …) — legitimate IPA/phonetic letters NFKC
# does NOT fold to ASCII, but an LLM reads them as normal text. Folding them here (a
# 1->1 position-preserving substitution) makes canonicalize_for_detection — and thus
# detect_pii/detect_secrets AND context_guard's canonical injection scan — resistant to
# small-caps smuggling of both attack phrases and PII/secrets. (q/x have no small-cap.)
_SMALLCAP_MAP = {
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f", "ɢ": "g", "ʜ": "h",
    "ɪ": "i", "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o", "ᴘ": "p",
    "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z",
}


def _canonicalize_with_map(text: str):
    """Return ``(canonical_text, index_map)`` using only 1->1 subs and 1->0 removals.

    ``index_map[k]`` is the offset in ``text`` of canonical char ``k`` so a canonical
    match span maps back to the exact original substring to mask.
    """
    if not text:
        return "", []
    src = text[:_CANON_MAX_LEN]
    out_chars: List[str] = []
    idx_map: List[int] = []
    for i, ch in enumerate(src):
        cp = ord(ch)
        # G18: Unicode Tag block "ASCII smuggling". TAG SPACE..TAG TILDE
        # (U+E0020..U+E007E) mirror printable ASCII 0x20..0x7E but are category Cf, so
        # the drop below would silently REMOVE them — hiding tag-encoded PII/secrets
        # from detection while the original tag bytes still egress (LLMs decode them).
        # DECODE the printable mirror back to ASCII here (BEFORE the Cf-drop). It is a
        # 1->1 position-preserving substitution, so index_map[k]=i still masks the
        # match back onto the original tag bytes. Tag controls (U+E0000/E0001/E007F)
        # are also Cf and fall through to the drop below.
        if 0xE0020 <= cp <= 0xE007E:
            out_chars.append(chr(cp - 0xE0000))
            idx_map.append(i)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Mn", "Me"):                 # invisibles / combining marks -> drop
            continue
        if cat == "Cc" and ch not in "\t\n\r":         # control chars -> drop (keep whitespace)
            continue
        nc = unicodedata.normalize("NFKC", ch)
        ch2 = nc if len(nc) == 1 else ch               # keep 1->1 compat folds (fullwidth/math/circled)
        if ch2 in _DASH_CHARS:
            ch2 = "-"
        elif unicodedata.category(ch2) == "Zs":
            ch2 = " "
        elif ch2 in _CONFUSABLE_MAP:
            ch2 = _CONFUSABLE_MAP[ch2]
        elif ch2 in _SMALLCAP_MAP:            # G21: small-caps -> ASCII (1->1)
            ch2 = _SMALLCAP_MAP[ch2]
        out_chars.append(ch2)
        idx_map.append(i)
    return "".join(out_chars), idx_map


def canonicalize_for_detection(text: str) -> str:
    """Public canonical form for obfuscation-resistant matching (no index map)."""
    return _canonicalize_with_map(text)[0]


# G44/G50: inline markdown emphasis/code markers sitting BETWEEN two word/PII chars are a
# typographic obfuscation — ``1**2**3-45-6789`` / ``john`@`example.com`` keep the raw bytes
# off the regexes yet a markdown renderer shows the value. Only ``*`` and `` ` `` are
# stripped: per CommonMark, INTRA-WORD ``_`` is NOT emphasis (``sk_live_key`` / ``snake_case``
# / ``12_3_`` render LITERALLY), so stripping ``_`` would both create false hits and DESTROY
# a legitimate ``sk_live_``/token prefix (masking the credential detector). Emphasis that
# WRAPS a whole token (space-adjacent ``**bold**`` / ``_italic_``) is left intact. Disjoint
# char classes on both sides -> linear (no ReDoS).
_MD_EMPH_INTERLEAVE = re.compile(r"(?<=[\w@.\-])[*`]+(?=[\w@.\-])")

# G51: markup a renderer DROPS (renders to nothing) — an attacker splits a value with it
# to evade byte-level matching while it visually reassembles: HTML COMMENTS (<!-- … -->),
# EMPTY paired tags (<span ...></span>), and self-closing/void tags (<br/>). Each subpattern
# is bounded — ``.*?`` is closed by ``-->``, ``[^>]*`` is a negated class — so LINEAR (no
# ReDoS). Detection-only (reveals the hidden value); never mutates the delivered egress.
_RENDER_INVISIBLE_HTML = re.compile(
    r"<!--.{0,400}?-->"                              # HTML comment (body capped -> ReDoS-safe)
    r"|<([a-zA-Z][a-zA-Z0-9]*)\b[^>]{0,400}>\s*</\1\s*>"  # <tag ...></tag>  (empty paired)
    r"|<[a-zA-Z][a-zA-Z0-9]*\b[^>]{0,400}/\s*>",         # <tag .../>       (self-closing)
    re.DOTALL,
)


def strip_interleaved_emphasis(text: str) -> str:
    """Remove markers interleaved among word/PII chars that a markdown/HTML renderer drops,
    exposing a value split to evade byte-level matching: markdown emphasis (``*`` / `` ` ``)
    AND render-invisible HTML (comments / empty tags — G51). Detection-only helper."""
    return _RENDER_INVISIBLE_HTML.sub("", _MD_EMPH_INTERLEAVE.sub("", text))


# --- bounded transport decode (G2): surface PII/secrets hidden in base64/hex ---
_B64ISH_RE = re.compile(r"[A-Za-z0-9+/]{12,}={0,2}")
_HEXISH_RE = re.compile(r"(?:[0-9A-Fa-f]{2}){8,}")
# G97: base32 (A-Z2-7). Its alphabet is a subset of base64's, so the base64 pass tries these
# tokens first and yields garbage (gated out) — a PII/secret laundered through base32 therefore
# leaked. Decode them as base32 here. Printable-ratio gated => FP-safe (parity with scanner G97).
_B32ISH_RE = re.compile(r"[A-Z2-7]{16,}={0,6}")
# G98: base85 (RFC1924). Only tokens carrying a b85-ONLY char (never a base64/base32/hex blob) are
# decoded, so no cross-decode FP. Printable-ratio gated. Parity with scanner G98.
_B85ISH_RE = re.compile(r"[0-9A-Za-z!#$%&()*+;<=>?@^_`{|}~-]{14,}")
_B85_ONLY_CHARS = frozenset("!#$%&()*;<>?@^_`{|}~-")
# CHG-0060: the decode scan is bounded by a decoded-BYTE budget, not a token COUNT.
# The old count cap (12) let a result hide an encoded secret past 12 decoy tokens
# (``<12 benign base64 blobs> <base64(secret)>`` -> the secret token was never decoded
# -> leaked). The input is already capped at _CANON_MAX_LEN, so decoding EVERY token in
# it is inherently bounded work; the byte budget below is the real DoS bound (it caps
# total decoded bytes incl. nested layers). _MAX_DECODE_TOKENS is now a high backstop on
# the number of detect() calls — set above the max tokens a _CANON_MAX_LEN input can hold
# (20000/12 ~= 1666 base64 tokens), so it never truncates a valid-length input.
_MAX_DECODE_TOKENS = 4096
_MAX_DECODE_BYTES = 4096
# Global decoded-byte budget per scan (sum of all decoded blob sizes, incl. nested
# layers). Sized well above the ~15KB a 20K base64 input can yield (with nesting), so it
# never limits legitimate input; it bounds a pathological nested/large decode-bomb.
_MAX_DECODE_TOTAL_BYTES = 262144
# G22: max nested encoding layers to follow (double-base64 / base64-of-hex "prompt
# laundering"). Depth is bounded and each layer is size + printable capped, so the
# recursion is decode-bomb safe.
_MAX_DECODE_DEPTH = 3
# CHG-0056: percent/URL-encoding obfuscation. A token carrying at least one %XX escape
# (PII/secret hidden in a URL query param, e.g. ``john.doe%40example.com``, or a
# %-encoded SSN) breaks the raw patterns but is trivially recoverable. CHG-0060: bounded
# by the same high backstop over the _CANON_MAX_LEN-capped input (was 32, which let a
# %-encoded secret hide past the 32nd percent-token).
_PERCENT_TOKEN_RE = re.compile(
    r"[A-Za-z0-9._~%@+/:=?&|-]*%[0-9A-Fa-f]{2}[A-Za-z0-9._~%@+/:=?&|-]*"
)
_MAX_URL_DECODE_TOKENS = 4096

# G85: Cf-tolerant OUTPUT masking of entity/percent-encoded and markdown-split PII/secret. The raw
# neutralizers/decoders are Cf-blind (contiguous-run regexes), so a value split with zero-width/bidi/
# format (Cf) chars evaded masking even after detection was made Cf-aware. These runs are matched on
# the Cf-STRIPPED view (``_tnorm`` in ``_redact_obfuscated``) and the span is mapped BACK onto the
# original bytes (with the interleaved Cf) via the canon index map. Bounded (possessive value runs /
# fixed entity+percent tokens) -> ReDoS-safe; masked only when the decode/strip reveals PII/secret.
_ENTITY_RUN_RE = re.compile(r"(?:&#x[0-9A-Fa-f]{1,6};|&#[0-9]{1,7};){2,}")
_PCT_RUN_RE = re.compile(r"(?:%[0-9A-Fa-f]{2}){2,}")
_MD_SPLIT_RUN_RE = re.compile(r"[\w@.\-]{1,256}+(?:[*`]{1,8}[\w@.\-]{1,256}+){1,256}")


def _decode_entity_run(run: str) -> str:
    def _cp(n: int) -> str:
        return chr(n) if 0 <= n < 0x110000 else ""
    s = re.sub(r"&#x([0-9A-Fa-f]{1,6});", lambda m: _cp(int(m.group(1), 16)) or m.group(0), run)
    s = re.sub(r"&#([0-9]{1,7});", lambda m: _cp(int(m.group(1))) or m.group(0), s)
    return s


def _printable_ratio(s: str) -> float:
    if not s:
        return 0.0
    return sum(1 for c in s if c.isprintable() or c.isspace()) / len(s)


def _decode_one(tok: str, is_hex: bool):
    """Decode a single base64/hex token to mostly-printable UTF-8, else ``None``."""
    try:
        if is_hex:
            raw = bytes.fromhex(tok)
        else:
            raw = base64.b64decode(tok + "=" * (-len(tok) % 4), validate=False)
        if not (0 < len(raw) <= _MAX_DECODE_BYTES):
            return None
        dec = raw.decode("utf-8")
    except Exception:
        return None
    # G26: a base64/hex-wrapped unicode-obfuscated payload (base64 ∘ zero-width, or
    # base64 ∘ Unicode-tags where EVERY char is a Cf tag) decodes to a string that is
    # legitimately "not printable" / all-format-chars, dragging _printable_ratio below
    # the gate — yet it IS the smuggled value. Judge printability on the CANONICAL form
    # (tags decoded to ASCII, zero-width/format stripped, homoglyphs folded) so the
    # compound evasion survives to detection/masking; genuine binary garbage still
    # canonicalizes to a low-printable residue and is dropped. The RAW ``dec`` is
    # returned (detect_*/redact_all canonicalize it again, mapping masks to originals).
    probe = canonicalize_for_detection(dec)
    return dec if probe and _printable_ratio(probe) >= 0.8 else None


_ALL_HEX_RE = re.compile(r"[0-9a-fA-F]+")


def _decode_one_b32(tok: str):
    """G97: decode a single base32 token to mostly-printable UTF-8, else ``None``."""
    try:
        core = tok.rstrip("=")
        raw = base64.b32decode(core + "=" * (-len(core) % 8), casefold=False)
        if not (0 < len(raw) <= _MAX_DECODE_BYTES):
            return None
        dec = raw.decode("utf-8")
    except Exception:
        return None
    probe = canonicalize_for_detection(dec)
    return dec if probe and _printable_ratio(probe) >= 0.8 else None


def _decode_one_b85(tok: str):
    """G98: decode a single RFC1924 base85 token to mostly-printable UTF-8, else ``None``.
    Caller gates on ``_B85_ONLY_CHARS`` so a base64/hex blob is never re-decoded as b85."""
    try:
        raw = base64.b85decode(tok)
        if not (0 < len(raw) <= _MAX_DECODE_BYTES):
            return None
        dec = raw.decode("utf-8")
    except Exception:
        return None
    probe = canonicalize_for_detection(dec)
    return dec if probe and _printable_ratio(probe) >= 0.8 else None


def _decode_nested(layer: str):
    """A decoded blob may itself be another encoding layer. Find the first base64/hex
    token in ``layer`` and decode it; returns the next-layer text or ``None``. A pure-
    hex string is also valid base64, so prefer HEX when the whole layer is hex (else
    base64-of-hex laundering mis-decodes)."""
    layer = layer.strip()
    if len(layer) >= 16 and len(layer) % 2 == 0 and _ALL_HEX_RE.fullmatch(layer):
        dec = _decode_one(layer, True)
        if dec is not None:
            return dec
    for regex, is_hex in ((_B64ISH_RE, False), (_HEXISH_RE, True)):
        m = regex.search(layer)
        if m:
            dec = _decode_one(m.group(0), is_hex)
            if dec is not None:
                return dec
    return None


def _iter_transport_decodes(text: str):
    """Yield ``(outer_token, decoded_text)`` for base64/hex blobs decoding to clean UTF-8,
    following up to ``_MAX_DECODE_DEPTH`` NESTED encoding layers (double-base64 prompt
    laundering). ``outer_token`` is always the OUTERMOST token as it appears in ``text``,
    so a caller masking ``outer_token`` removes the whole encoded blob from the original
    bytes even when the sensitive value is buried several layers deep. Token-count + size
    + depth capped, mostly-printable-only => decode-bomb / FP safe.
    """
    if not text:
        return
    scan = text[:_CANON_MAX_LEN]
    # CHG-0060: iterate ALL tokens in the capped input, bounded by a global decoded-byte
    # budget (shared across the base64 + hex passes) rather than a per-pass token count,
    # so a decoy-padded result can no longer hide an encoded secret past a fixed token
    # position. budget + input cap + per-token size cap + depth cap => decode-bomb safe.
    budget = _MAX_DECODE_TOTAL_BYTES
    for regex, is_hex in ((_B64ISH_RE, False), (_HEXISH_RE, True)):
        seen = 0
        for m in regex.finditer(scan):
            if seen >= _MAX_DECODE_TOKENS or budget <= 0:
                break
            seen += 1
            top_tok = m.group(0)
            dec = _decode_one(top_tok, is_hex)
            if dec is None:
                continue
            budget -= len(dec)
            yield top_tok, dec
            # G22: follow nested layers, always reporting the OUTER token so masking
            # lands on the original bytes.
            layer = dec
            for _ in range(_MAX_DECODE_DEPTH - 1):
                if budget <= 0:
                    break
                nxt = _decode_nested(layer)
                if nxt is None or nxt == layer:
                    break
                budget -= len(nxt)
                yield top_tok, nxt
                layer = nxt
    # G97: base32 pass (A-Z2-7). Runs after base64/hex over the same budget; the decoded
    # payload follows nested base32∘base64/hex layers, always reporting the OUTER token.
    seen32 = 0
    for m in _B32ISH_RE.finditer(scan):
        if seen32 >= _MAX_DECODE_TOKENS or budget <= 0:
            break
        seen32 += 1
        top_tok = m.group(0)
        dec = _decode_one_b32(top_tok)
        if dec is None:
            continue
        budget -= len(dec)
        yield top_tok, dec
        layer = dec
        for _ in range(_MAX_DECODE_DEPTH - 1):
            if budget <= 0:
                break
            nxt = _decode_nested(layer)
            if nxt is None or nxt == layer:
                break
            budget -= len(nxt)
            yield top_tok, nxt
    # G98: base85 pass (RFC1924). Gated on _B85_ONLY_CHARS so a base64/hex blob is never
    # re-decoded as b85 (no cross-decode FP); shares the budget, follows nested layers.
    seen85 = 0
    for m in _B85ISH_RE.finditer(scan):
        if seen85 >= _MAX_DECODE_TOKENS or budget <= 0:
            break
        top_tok = m.group(0)
        if not any(c in _B85_ONLY_CHARS for c in top_tok):
            continue
        seen85 += 1
        dec = _decode_one_b85(top_tok)
        if dec is None:
            continue
        budget -= len(dec)
        yield top_tok, dec
        layer = dec
        for _ in range(_MAX_DECODE_DEPTH - 1):
            if budget <= 0:
                break
            nxt = _decode_nested(layer)
            if nxt is None or nxt == layer:
                break
            budget -= len(nxt)
            yield top_tok, nxt
            layer = nxt


def _iter_transport_decodes_canon(text: str, canon: str):
    """G75/G76: yield decoded payloads from transport (base64/hex) blobs across several views of
    the text, de-duplicated:
      * RAW and CANONICAL (Cf-stripped) forms — G75: catches a token split by invisible format
        chars (e.g. U+061C ALM interleaved through the blob) that the raw token regex misses.
      * a WHITESPACE-COLLAPSED form — G76: catches a base64/hex blob split by ASCII spaces/newlines
        ("MTIz LTQ1 LTY3 ODk="), which the contiguous token regex never reassembles even though a
        lenient decoder (and most LLMs) ignore whitespace and recover the payload.
    The existing printable + detect gates keep this FP-safe: ordinary prose collapses to
    high-entropy bytes that neither stay printable nor match any detector. When there is nothing
    to add (plain ASCII, no whitespace) this is exactly one pass, so plain-text behaviour is
    unchanged."""
    seen: set[str] = set()
    sources: list[str] = [text]
    if canon != text:
        sources.append(canon)
    # G76: collapse ASCII whitespace so space/newline-split base64/hex tokens reassemble.
    for base in (text, canon):
        ws = re.sub(r"\s+", "", base)
        if ws != base and ws not in sources:
            sources.append(ws)
    for source in sources:
        for _tok, dec in _iter_transport_decodes(source):
            if dec in seen:
                continue
            seen.add(dec)
            yield dec


# B2-redactor-coverage: a trailing 10-digit US phone that may carry a SINGLE
# separator (space / dot / hyphen) between groups. Used ONLY in the
# context-gated ``phone_us_bare_contextual`` pattern below, so a phone cue
# always precedes it — keeping order-id / revenue runs (which never carry a
# phone cue) untouched. The branches enumerate every realistic grouping whose
# digits sum to exactly 10 (contiguous, 5+5, 4+6, 3-3-4, 3+7); the trailing
# ``\b`` plus the fixed total prevents swallowing a longer numeric id (an
# 11+ digit run fails ``\b`` after 10 contiguous digits and has no matching
# split branch, so it is left raw rather than partially masked). Every
# quantifier is fixed and each separator is mandatory in its branch — no
# ambiguous optional-repeat, so it stays LINEAR-time (no ReDoS).
_BARE_PHONE_10_SPLIT = (
    r"(?:\d{10}"
    r"|\d{5}[\s.\-]\d{5}"
    r"|\d{4}[\s.\-]\d{6}"
    r"|\d{3}[\s.\-]\d{3}[\s.\-]\d{4}"
    r"|\d{3}[\s.\-]\d{7})"
)


PII_PATTERNS: Dict[str, str] = {
    "credit_card": r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    # SSN written with single spaces instead of dashes ("123 45 6789"). The
    # distinctive 3-2-4 single-space grouping is conservative: ordinary prose
    # rarely produces that exact shape. Each group is a fixed quantifier, so
    # this is LINEAR-time (no backtracking).
    "ssn_spaced": r"\b\d{3}\s\d{2}\s\d{4}\b",
    # SSN with NO separators ("123456789"). A bare 9-digit run is far too common
    # (order ids, routing numbers, etc.) to match unconditionally, so this is
    # CONTEXT-BOUNDED: it only fires when an explicit SSN keyword immediately
    # precedes the 9 digits. The context prefix is a fixed alternation and the
    # value is a fixed \d{9}, so it stays linear-time.
    "ssn_nosep": r"\b(?:ssn|social\s+security(?:\s+(?:number|no\.?|#))?)\b[\s:#]*\d{9}\b",
    # Bounded quantifiers (RFC-ish: local<=64, each label<=63, <=8 labels,
    # TLD 2-24 letters) keep this LINEAR-time. The previous unbounded form
    # `[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}` catastrophically
    # backtracks on long [A-Za-z0-9.-] runs lacking an '@'/TLD (ReDoS: a 500KB
    # field hung ~18 min). This is the single source of truth reused by the
    # scanner, context_guard, detect_pii/redact_all, and the RAG redactor.
    # Trailing ``(?!:[^\s/]+/)`` rejects SSH/git remote syntax (``git@github.com:org/repo``)
    # where a colon is immediately followed by a path segment — those are not
    # email addresses and must not be redacted. A real email followed by a colon
    # ("contact john@x.com: ...") is NOT rejected because the colon is followed
    # by whitespace, not a ``path/``. The lookahead has no nested quantifier, so
    # it stays linear-time.
    "email": r"\b[A-Za-z0-9._%+\-]{1,64}@(?:[A-Za-z0-9\-]{1,63}\.){1,8}[A-Za-z]{2,24}\b(?!:[^\s/]+/)",
    # US phone. A bare 10-digit run ("9876543210") is an order id / revenue
    # figure far more often than a phone number, so at least ONE separator (space,
    # dot, or hyphen) OR a literal area-code paren is now MANDATORY between digit
    # groups — the old "all separators optional" form false-positived on every
    # 10-digit integer. Two NON-OVERLAPPING alternatives, both with an optional
    # ``+1`` prefix:
    #   * parenthesised area code ("(415) 555-0142") — distinguished by ``\(...\)``
    #   * three groups joined by MANDATORY separators ("415-555-0142",
    #     "415.555.0142", "+1 415 555 0142")
    # Exactly 3-3-4 digit grouping, so version strings ("1.2.3") and dotted-quad
    # IPs ("192.168.1.1") do not fit. Every quantifier is fixed and separators are
    # mandatory (no ambiguous optional-repeat) — LINEAR-time, no ReDoS.
    "phone_us": (
        r"(?:\+1[\s.\-]?)?"
        r"(?:\(\d{3}\)[\s.\-]?\d{3}[\s.\-]?\d{4}|\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b)"
    ),
    # International / E.164 phone ("+44 20 7946 0958", "+919876543210"). Anchored
    # on a leading '+' (the distinctive E.164 marker) so it does not match bare
    # digit runs. Two NON-OVERLAPPING alternatives:
    #   * contiguous 10-15 digits (full E.164 with no separators), or
    #   * country code + at least two separated groups (rejects "+1 2" / "+100").
    # Every group is bounded and separators between groups are mandatory in the
    # separated branch, so there is no nested/ambiguous repeat — LINEAR-time.
    # Added `\d{1,4}[\s\-]?\d{6,12}` branch so `+<country>-<run-together digits>`
    # (e.g. +91-8088054321) is masked. The previous pattern only caught fully
    # run-together (`\d{10,15}` right after `+`) or separator-grouped numbers, so an
    # international number with the country code split off by a single dash leaked —
    # incl. through redact_all (the scrubber used on guard-model evidence/advisory).
    # B2 rigor: the grouped branch capped each group at 4 digits, so the extremely
    # common 5-digit grouping ("+91 98765 43210", Indian/EU mobiles) escaped while the
    # docstring claimed to cover separated international numbers. The cap is now {1,5}
    # so 5+5 groupings are masked too. FP-safety is structural — the leading '+' is a
    # distinctive E.164 marker (order-id / revenue runs never carry it) and every group
    # is a fixed bounded quantifier under a single non-nested repeat, so it stays
    # LINEAR-time (no ReDoS).
    # G38: widen the grouped-international branch's per-group max from 5 to 7 digits
    # so UK-style numbers (+44 7911 123456 — a 6-digit trailing group) redact like
    # +91/+1 already do; enforcement-consistency for international phone PII. Still
    # requires the leading '+' AND >=2 grouped runs, so benign "+5 -3 +2"/"score
    # +10 +20 +30" do NOT match (verified 0 FP).
    "phone_intl": r"\+(?:\d{10,15}|\d{1,4}[\s\-]?\d{6,12}|\d{1,3}(?:[\s\-]\d{1,7}){2,6})\b",
    # Dotted phone ("415.555.0142"). The 3.3.4 dotted grouping is distinctive;
    # fixed quantifiers keep it linear and the \b bounds avoid swallowing
    # adjacent digits. Version strings ("1.2.3") and dotted-quad IPs do not fit
    # the exact 3.3.4 digit-count shape.
    "phone_dotted": r"\b\d{3}\.\d{3}\.\d{4}\b",
    # Contextual bare 10-digit US phone. Tier-2 detects these semantically, but
    # phone_us intentionally skips separatorless runs (order IDs, revenue figures).
    # Only match when an explicit phone/contact lead-in immediately precedes the
    # digits so enforcement redaction and the upstream LLM never see raw PII the
    # guard model already flagged — while order ids ("order 8929554991") never match.
    #
    # WIRE-CAPTURE LEAK FIX: the original branch covered only "phone/mobile/cell/tel
    # [number] is/:" and MISSED the most common phrasing — an imperative contact verb
    # ("call/text/reach me at 8929554991"). A real-fleet mitmproxy capture caught the
    # bare phone forwarded RAW to OpenRouter: detect_pii saw only a co-occurring SSN,
    # so redacted_content kept the phone raw, and _apply_redaction's digit backstop
    # (which preserves any run already present in redacted_content as a value the
    # firewall chose to keep) let it ride. Detecting it HERE masks it everywhere
    # downstream — verdict, redact_pii/redacted_content, redact_all, and the backstop.
    # B2: the trailing value broadened from contiguous ``\d{10}`` to
    # ``_BARE_PHONE_10_SPLIT`` so separator-split 10-digit phones behind a phone
    # cue ("please call me at 89295 54991" — the G0 5+5 leak) are also masked.
    # Context-gating is unchanged, so order-id / revenue runs (no phone cue) are
    # still untouched.
    # B4 (path-divergence leak): the imperative branch required the contact verb to
    # IMMEDIATELY precede "at/on" (optionally "me/us back"), so natural object words
    # ("call THE CUSTOMER BACK at 8929554991", "call BACK THE CUSTOMER at ...") and a
    # cue split across the sentence ("reach me at bob@corp.example OR ON 8929554991")
    # slipped through — the bare phone then egressed RAW at the RAG-ingest / embeddings
    # path (whose backstop only masks runs the firewall already removed) while the
    # QUERY path's blanket digit backstop masked it, a redaction DIVERGENCE between the
    # two embedding paths. The gap is now a BOUNDED, DIGIT-FREE, single-line lazy run
    # (``[^\d\n]{0,40}?``): still gated on a contact verb + "at/on" + a 10-digit value,
    # so order-id / revenue runs (no contact verb) stay untouched, and because the gap
    # contains no digits the trailing phone is the only maskable run in the span. The
    # quantifier is bounded over a negated char class (no nested repeat) — LINEAR-time.
    "phone_us_bare_contextual": (
        r"(?:"
        # "phone/mobile/cell/tel [number] is/:" 8929554991
        r"\b(?:phone|mobile|cell|tel(?:ephone)?)\s*(?:number|no\.?|#)?\s*(?:is|:)\s*"
        # imperative contact: "call/text/dial/ring/sms/reach/contact ...<=40 non-digit
        # chars, same line...> at/on" 8929554991
        r"|\b(?:call|text|dial|ring|sms|reach|contact|phone)\b[^\d\n]{0,40}?\b(?:at|on)\s+"
        # B2 rigor (sms/whatsapp direct-adjacency leak): a STRONG dialing/messaging verb
        # placed IMMEDIATELY before the number with no "at/on" connector ("sms 89295 54991",
        # "whatsapp 8929554991", "dial 4155550142") leaked raw because the at/on branch above
        # demands the connector and Tier-2 did not flag it. The gap here is whitespace/colon
        # ONLY (``[:\s]+``) — no intervening words or digits — so the curated phone verb must
        # sit directly on the value; order-id phrasings ("call 5000 customers", "text the
        # 1234567890 line") never match because the 10-digit grouping must START right after
        # the cue. Verbs limited to unambiguous dialing/messaging cues (no "message"/"msg"/
        # "reach"/"contact" here — those stay gated on at/on) to keep FP near-zero.
        r"|\b(?:call|text|dial|ring|sms|whatsapp|telegram|imessage)\b[:\s]+"
        # possessive: "my/the [phone/mobile/cell] number/no/# [is]" 8929554991
        r"|\b(?:my|the)\s+(?:phone\s+|mobile\s+|cell\s+)?(?:number|no\.?|#)\s+(?:is\s+)?"
        r")" + _BARE_PHONE_10_SPLIT + r"\b"
    ),
    # G25: MAC address (device identifier / personal data under GDPR). The 6-hex-pairs
    # colon/hyphen format is highly distinctive => near-zero FP (a 3-group time like
    # 12:34:56 has too few groups; fixed 5 repetitions => linear, no ReDoS).
    "mac_address": r"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b",
    # G25: government / national identity numbers (passport, Aadhaar, UK NINO, driver's
    # licence). A BARE value is indistinguishable from any id/order number, so gate on
    # the canonical cue word AND require the value to actually contain a digit within
    # its first few chars (a bounded lookahead) so benign prose like "passport
    # application form" is not flagged. All quantifiers are bounded => linear/ReDoS-safe.
    "government_id": (
        # G94: also the BRITISH term "driving licence/license" (the standard UK phrasing for a
        # driver's license) — previously only the US "driver'?s? licen[cs]e" cue was recognised, so a
        # UK "driving licence number <X>" egressed undetected. driv(?:er'?s?|ing) covers both.
        r"(?:passport|aadhaar|aadhar|national\s+insurance|driv(?:er'?s?|ing)\s+licen[cs]e|\bnino\b)"
        r"\s*(?:number|no\.?|id|#)?\s*[:#]?\s*"
        r"(?=[A-Za-z0-9\s\-]{0,6}\d)"
        r"([A-Za-z0-9][A-Za-z0-9\s\-]{4,16}[A-Za-z0-9])"
    ),
    # N-CRED FIX: the original r"\bsk-[a-zA-Z0-9]{32,}\b" required an UNBROKEN
    # alphanumeric run, so it MISSED every modern hyphenated key format —
    # OpenAI project/service keys (sk-proj-…, sk-svcacct-…, sk-admin-…) and
    # OpenRouter keys (sk-or-v1-…). Those leaked through the OUTPUT credential
    # detector as a soft "flag" instead of a hard redact/block under a block
    # policy. Match the known modern prefixes (high-entropy tail) AND keep the
    # classic 32-char form. Prefix-anchored + a 16+ char tail keeps false
    # positives on benign "sk-…" prose negligible.
    "api_key_openai": r"\bsk-(?:proj|svcacct|admin|or-v1|or|live|test)-[a-zA-Z0-9_-]{16,}\b|\bsk-[a-zA-Z0-9]{32,}\b",
    "aws_access_key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    # AWS SECRET access key — the high-value credential. It has no fixed prefix
    # (40 chars of [A-Za-z0-9/+]), so match it in context of its variable name to
    # avoid false positives on arbitrary base64/hash blobs. Without this the
    # output guard masked only the AKIA id and egressed the secret in cleartext.
    "aws_secret_access_key": r"(?i)\baws[_-]?secret[_-]?access[_-]?key\b\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{16,}",
    # G93: ALL GitHub token classes share the ``gh?_`` + 36 base62 format — ghp_ (classic PAT),
    # gho_ (OAuth), ghu_ (app user-to-server), ghs_ (app server-to-server), ghr_ (refresh). The
    # pattern previously matched only ghp_, so gho_/ghu_/ghs_/ghr_ tokens egressed undetected.
    "github_token": r"\bgh[pousr]_[a-zA-Z0-9]{36}\b",
    # CHG-0054: match the ENTIRE PEM block (BEGIN header + base64 BODY + END footer),
    # not just the BEGIN line — else redact_all masked only the header and the key
    # MATERIAL (the actual secret) egressed intact. Generic key-type prefix covers
    # RSA / EC / DSA / OPENSSH / ENCRYPTED / plain (the old pattern only matched RSA).
    # Falls back to consuming the base64 body when the END marker is absent (truncated
    # key) so the material can never survive redaction.
    "private_key_header": (
        r"-----BEGIN\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----"
        r"(?:[\s\S]*?-----END\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----|[A-Za-z0-9+/=\s]*)"
    ),
}

# Instructional / interrogative prose words that frequently follow a bare
# "password:" / "secret:" in benign educational or UX copy ("password: forgotten?",
# "secret: should never be shared"). A real credential value never *starts* with
# one of these, so a negative look-ahead lets us reject the prose variant without
# losing genuine secret detection.
_CREDENTIAL_PROSE_WORDS = (
    r"forgotten|forgot|instructions?|required|require|should|shouldn|never|must|"
    r"copy|least|reset|change|changed|changing|manager|management|protected|"
    r"field|here|your|yours|the|this|that|none|empty|blank|hidden|unknown|"
    r"please|click|enter|type|example|placeholder|redacted|masked|hashed"
)
# A captured value that resembles a real secret rather than prose: it must
# contain at least one digit OR one symbol (non-alphanumeric) — ordinary
# all-letter prose words are rejected — and must not *begin* with an
# instructional prose word.
_CREDENTIAL_VALUE = (
    r"(?!(?:" + _CREDENTIAL_PROSE_WORDS + r")\b)"
    r"(?=[^\s\"']*[\d\W])"
    r"[^\s\"']{8,}"
)
_TOKEN_VALUE = (
    r"(?!(?:" + _CREDENTIAL_PROSE_WORDS + r")\b)"
    r"(?=[a-zA-Z0-9_\-\.]*\d)"
    r"[a-zA-Z0-9_\-\.]{8,}"
)

SECRET_PATTERNS: Dict[str, str] = {
    # Require an actual assignment operator (':' or '=') between the keyword and
    # the value — NOT bare whitespace. Otherwise benign prose like "password
    # manager", "password-protected", "secret garden" or "token economy" is
    # falsely flagged/redacted as a leaked credential.
    #
    # Additionally require the captured VALUE to look like a real credential
    # (>= 8 chars with at least one digit/symbol, and not starting with an
    # instructional prose word) so educational copy such as
    # "password: should never be shared." is not mangled or hard-blocked.
    "password_assignment": r'password["\s]*[:=][\s"\']*' + _CREDENTIAL_VALUE,
    "secret_assignment": r'secret["\s]*[:=][\s"\']*' + _CREDENTIAL_VALUE,
    "token_assignment": r'token["\s]*[:=][\s"\']*' + _TOKEN_VALUE,
    # CHG-0055: api_key / apikey / access_key assignments are as sensitive as the
    # password/secret/token ones above but were NOT in the inventory — an
    # ``API_KEY=<non-provider-format-token>`` (e.g. below the openai 32-char
    # threshold) egressed UNMASKED. Same ``_TOKEN_VALUE`` guard (>=8 chars with a
    # digit, not an instructional prose word) so ``api_key=none`` / ``api key:
    # forgotten?`` stay false-positive-safe. Case-insensitive via compile_pattern.
    "api_key_assignment": r'(?:api[_-]?key|access[_-]?key)["\s]*[:=][\s"\']*' + _TOKEN_VALUE,
    # RAG-C5-CRED-COVERAGE: standalone credential FORMATS the assignment patterns
    # above miss. detect_secrets had no Slack token / JWT / Bearer inventory, so
    # these credential forms were stored unblocked at RAG ingest (and unredacted in
    # chat input). bearer_token already existed in CREDENTIAL_EXPOSURE_PATTERNS (the
    # output guard) — fold the class into the ingest secret inventory too.
    "slack_token": r'\bxox[baprs]-[0-9A-Za-z-]{10,}\b',
    "jwt": r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b',
    "bearer_token": r'Bearer\s+[A-Za-z0-9_\-\.]{20,}',
    # G24: modern cloud/registry credential FORMATS the inventory missed, so a bare
    # Google API key / npm token egressed to the model or was stored at RAG ingest.
    # Both have a distinctive fixed prefix + length => detection is near-zero-FP.
    "google_api_key": r'\bAIza[0-9A-Za-z_\-]{32,42}\b',
    "npm_token": r'\bnpm_[A-Za-z0-9]{32,42}\b',
    # AWS secret access keys are 40 bare base64 chars with NO prefix, so a bare value
    # is indistinguishable from any 40-char blob. Gate on the canonical key-name cue
    # (env var / config label) to stay low-FP; the standard AKIA access key is already
    # covered by aws_access_key above.
    "aws_secret_access_key": r'aws_secret_access_key["\s]*[:=][\s"\']*([A-Za-z0-9/+]{40})\b',
    # CHG-0071: real-world provider credential formats that egressed UNMASKED and were NOT
    # flagged by detect_secrets (found via an adversarial redact_all secret-format sweep).
    # Anthropic keys were missed while the OpenAI ``sk-`` family (api_key_openai) was caught;
    # SendGrid / GitLab PAT / Slack incoming-webhook had no inventory entry at all. All have
    # highly specific prefixes/structures => near-zero false-positive.
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",
    "sendgrid_key": r"\bSG\.[A-Za-z0-9_-]{16,32}\.[A-Za-z0-9_-]{32,}\b",
    "gitlab_pat": r"\bglpat-[A-Za-z0-9_-]{20,}\b",
    "slack_webhook": r"https://hooks\.slack\.com/services/[A-Za-z0-9/_+-]+",
    # CHG-0072: more distinctive-prefix provider tokens the sweep found egressing UNMASKED
    # and undetected. All have a fixed provider prefix + length => near-zero false positive.
    "digitalocean_pat": r"\bdop_v1_[a-f0-9]{64}\b",
    "shopify_token": r"\bshp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}\b",
    "square_token": r"\bsq0(?:atp|csp|idp)-[A-Za-z0-9_-]{22,}\b",
    "databricks_token": r"\bdapi[a-f0-9]{32}\b",
    "hashicorp_vault_token": r"\bhv[sb]\.[A-Za-z0-9_-]{20,}\b",
    "figma_token": r"\bfigd_[A-Za-z0-9_-]{20,}\b",
    "telegram_bot_token": r"\b(?:bot)?\d{8,10}:AA[A-Za-z0-9_-]{32,}\b",
    "pypi_token": r"\bpypi-[A-Za-z0-9_-]{16,}\b",
    "linear_api_key": r"\blin_api_[A-Za-z0-9]{32,}\b",
    "mailgun_key": r"\bkey-[0-9a-f]{32}\b",
}

PHI_PATTERNS: Dict[str, str] = {
    "medical_license": r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b",
    "medical_record": r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b",
    "insurance_id": r"\b(?:insurance\s+(?:id|number|#)|policy\s*#)\s*[:\s]*[A-Z0-9]{5,15}\b",
}

PCI_PATTERNS: Dict[str, str] = {
    # IBAN. The original matched only the CONTIGUOUS form
    # ("GB82WEST12345698765432") and missed the SPACED printed form
    # ("GB82 WEST 1234 5698 7654 32") that banks actually display. Second
    # alternative adds the canonical "blocks of 4 separated by single spaces"
    # grouping (2-7 four-char blocks + an optional 1-4 char tail). Interior
    # groups are fixed at 4 chars with a MANDATORY single-space anchor each
    # iteration, so the match cannot bleed into following prose (a 9-letter word
    # like "confirmed" is not a 4-char group) and there is no ambiguous
    # optional-repeat — LINEAR-time, no ReDoS.
    "iban_code": (
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b"
        r"|\b[A-Z]{2}\d{2}(?:\s[A-Z0-9]{4}){2,7}(?:\s[A-Z0-9]{1,4})?\b"
    ),
    "us_bank_number": r"\b(?:account|routing)\s*#?\s*[:\s]*\d{8,17}\b",
    "crypto_address": r"\b(?:0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})\b",
}

HALLUCINATION_PATTERNS: Dict[str, str] = {
    # Narrowed: bare "but/however/might" in safety refusals caused false positives.
    "uncertainty_hedge": (
        r"\b(?:I think|I believe|probably|possibly|"
        r"I'm not (?:sure|certain)|as far as I know)\b"
    ),
    "fabricated_citation": (
        r"(?:according to|as stated (?:in|by)|per the (?:study|report|paper|article))\s+"
        r"(?:(?:Dr\.\s+)?[A-Z][a-z]+ (?:et al\.?|and colleagues)|\[\d+\])"
    ),
    "confidence_disclaimer": (
        r"(?:I (?:cannot|can't) verify (?:this|that|the)|"
        r"I do not have (?:access to|information about)|"
        r"this (?:may|might) not be (?:accurate|correct|up to date))"
    ),
    "contradictory_statement": (
        r"contrary to (?:what I (?:just )?said|the above|this)|"
        r"on the other hand,?\s+(?:this|that)\s+(?:is|was)\s+(?:not|incorrect|wrong)"
    ),
    "fabricated_statistic": (
        r"(?:approximately|roughly|about|nearly)\s+\d+(?:\.\d+)?%?\s+"
        r"(?:of|percent|per cent)"
    ),
    "nonexistent_entity": (
        r"(?:the\s+(?:University|Institute|Organization|Foundation|Agency)"
        r"\s+of\s+[A-Z][a-z]+\s+[A-Z][a-z]+)"
    ),
    "temporal_impossibility": (
        r"(?:as of\s+\d{4}|in\s+(?:19|20)\d{2})\s+.*?"
        r"(?:will|is going to|plans to|expects to)"
    ),
    "source_attribution_gap": (
        r"(?:research (?:shows|indicates|suggests|proves)|"
        r"studies (?:show|indicate|suggest|prove)|"
        r"experts (?:say|agree|believe))\b"
    ),
}

IP_LEAKAGE_PATTERNS: Dict[str, str] = {
    "internal_ipv4": (
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3})\b"
    ),
    "internal_hostname": r"\b(?:[a-z][a-z0-9-]+\.(?:internal|local|corp|intra|private|lan))\b",
    # Skip canonical *public* doc/install paths (nginx/apache config dirs, the
    # /usr/{share,bin,sbin,lib,local} tree, and any /opt/<app> install root) so
    # standard documentation examples don't pollute the §1.7 flagged-output
    # telemetry. Private/sensitive paths (/home/<user>, /etc/passwd, /var/...)
    # still match.
    "file_path_unix": (
        r"(?:/"
        r"(?!(?:etc/(?:nginx|apache2)|usr/(?:share|bin|sbin|lib|local)|"
        r"opt/[a-zA-Z0-9_.-]+)(?:[/]|$))"
        r"(?:home|var|etc|opt|usr|tmp|srv)/[a-zA-Z0-9_./-]{3,})"
    ),
    # Skip C:\Users\Public and C:\Program Files (standard public locations);
    # keep per-user, Windows, AppData and temp paths.
    "file_path_windows": (
        r"(?:[A-Z]:\\"
        r"(?!(?:Users\\Public|Program Files)(?:\\|$))"
        r"(?:Users|Windows|Program Files|temp|AppData)\\[a-zA-Z0-9_.\\ -]{3,})"
    ),
    "internal_url": (
        r"https?://(?:(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3})|"
        r"[a-z][a-z0-9-]+\.(?:internal|local|corp))[:/]"
    ),
    # CHG-0073: internal IPv6 leakage. internal_ipv4 (above) was IPv4-only, so a
    # ULA (fc00::/7 -> fc/fd first hextet) or link-local (fe80::/10 -> fe8..feb)
    # address — which reveals internal network topology exactly like an RFC1918
    # IPv4 — egressed RAW in a tool RESULT. Anchored on the distinctive internal
    # first hextet (a FULL 4-hex hextet, so a 2-hex MAC group, a bare contiguous
    # hex blob, and an HH:MM:SS timestamp never match) followed by >=1 ':'/'::'
    # hextet group or a bare '::'. Loopback '::1' is intentionally NOT flagged (as
    # benign as localhost). Every quantifier is fixed/bounded and each repeat
    # consumes >=1 char => LINEAR-time (no ReDoS). Compiled re.IGNORECASE.
    "internal_ipv6": (
        r"\b(?:f[cd][0-9a-f]{2}|fe[89ab][0-9a-f])"
        r"(?:(?:::?[0-9a-f]{1,4})+(?:::)?|::)"
    ),
    # CHG-0073: link-local / cloud-metadata (169.254.0.0/16 — INCLUDING the
    # 169.254.169.254 IMDS endpoint that hands out cloud IAM creds) and CGNAT
    # (100.64.0.0/10, 2nd octet 64-127) IPv4. internal_ipv4 covered only RFC1918,
    # so an IMDS/CGNAT address egressed RAW in a tool RESULT (cloud-env / topology
    # disclosure — and IMDS is the very SSRF target the dial-time guards block).
    # Fixed quantifiers + \b => linear-time, near-zero FP (non-routable ranges).
    "link_local_ipv4": (
        r"\b(?:169\.254\.\d{1,3}\.\d{1,3}"
        r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"
    ),
}

CREDENTIAL_EXPOSURE_PATTERNS: Dict[str, str] = {
    "bearer_token": r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
    "basic_auth": r"Basic\s+[A-Za-z0-9+/=]{16,}",
    # The value must resemble an actual secret (>= 8 chars, contains a digit or
    # symbol, not an instructional prose word) so benign copy like
    # "password: forgotten? click reset" is not hard-blocked. See _CREDENTIAL_VALUE.
    "exposed_password": r"(?:password|passwd|pwd)\s*[:=]\s*['\"]?" + _CREDENTIAL_VALUE,
    "connection_string": r"(?:mongodb|mysql|postgres(?:ql)?|redis|amqp)://[^\s]{10,}",
    "private_key_block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
    # C4-CRED-INGEST-*: modern provider credential formats the assignment/url patterns
    # above miss. Folded into the MASTER inventory so the RAG-ingest path (which now
    # runs detect_credential_exposure) and the output guard share one credential set.
    "github_fine_grained_pat": r"\bgithub_pat_[A-Za-z0-9_]{22,}\b",
    # G93: Stripe RESTRICTED keys (rk_live_/rk_test_) share the sk_ secret-key format and are a
    # live credential — include the rk_ prefix so a restricted key is not egressed undetected.
    "stripe_key": r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b",
    "azure_storage_key": r"AccountKey=[A-Za-z0-9+/=]{40,}",
    "twilio_api_key": r"\bSK[0-9a-fA-F]{32}\b",
    "gcp_service_account_key": r'"private_key"\s*:\s*"-----BEGIN PRIVATE KEY-----',
    "slack_token": r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
}

COMPLIANCE_TAG_MAP: Dict[str, List[str]] = {
    "credit_card": ["PCI-DSS"],
    "ssn": ["PII", "HIPAA"],
    "ssn_spaced": ["PII", "HIPAA"],
    "ssn_nosep": ["PII", "HIPAA"],
    "email": ["PII", "GDPR"],
    "phone_us": ["PII", "GDPR"],
    "phone_intl": ["PII", "GDPR"],
    "phone_dotted": ["PII", "GDPR"],
    "phone_us_bare_contextual": ["PII", "GDPR"],
    "mac_address": ["PII", "GDPR"],
    "government_id": ["PII", "GDPR"],
    "api_key_openai": ["SECRET"],
    "aws_access_key": ["SECRET"],
    "aws_secret_access_key": ["SECRET"],
    "google_api_key": ["SECRET"],
    "npm_token": ["SECRET"],
    "github_token": ["SECRET"],
    "private_key_header": ["SECRET"],
    "password_assignment": ["SECRET"],
    "secret_assignment": ["SECRET"],
    "token_assignment": ["SECRET"],
    "api_key_assignment": ["SECRET"],
    "anthropic_key": ["SECRET"],
    "sendgrid_key": ["SECRET"],
    "gitlab_pat": ["SECRET"],
    "slack_webhook": ["SECRET"],
    "digitalocean_pat": ["SECRET"],
    "shopify_token": ["SECRET"],
    "square_token": ["SECRET"],
    "databricks_token": ["SECRET"],
    "hashicorp_vault_token": ["SECRET"],
    "figma_token": ["SECRET"],
    "telegram_bot_token": ["SECRET"],
    "pypi_token": ["SECRET"],
    "linear_api_key": ["SECRET"],
    "mailgun_key": ["SECRET"],
    "medical_license": ["PHI", "HIPAA"],
    "medical_record": ["PHI", "HIPAA"],
    "insurance_id": ["PHI", "HIPAA"],
    "iban_code": ["PCI-DSS"],
    "us_bank_number": ["PCI-DSS"],
    "crypto_address": ["PCI-DSS"],
    "internal_ipv4": ["INFRA"],
    "internal_ipv6": ["INFRA"],          # CHG-0073
    "link_local_ipv4": ["INFRA"],        # CHG-0073 (incl. 169.254.169.254 IMDS)
    "internal_hostname": ["INFRA"],
    "file_path_unix": ["INFRA"],
    "file_path_windows": ["INFRA"],
    "internal_url": ["INFRA"],
    "bearer_token": ["SECRET", "SOC2"],
    "basic_auth": ["SECRET", "SOC2"],
    "exposed_password": ["SECRET", "SOC2"],
    "connection_string": ["SECRET", "SOC2"],
    "private_key_block": ["SECRET", "SOC2"],
    # CHG-0075: the rest of CREDENTIAL_EXPOSURE_PATTERNS had NO tag-map entry, so
    # get_compliance_tags returned [] — a detected Stripe/Twilio/Azure/GCP/Slack/JWT/
    # fine-grained-PAT credential was never tagged SECRET (breaks enforce-by-tag +
    # audit, and the arg credential force-block's SECRET-tag path). All are secrets.
    "github_fine_grained_pat": ["SECRET", "SOC2"],
    "stripe_key": ["SECRET", "SOC2"],
    "azure_storage_key": ["SECRET", "SOC2"],
    "twilio_api_key": ["SECRET", "SOC2"],
    "gcp_service_account_key": ["SECRET", "SOC2"],
    "slack_token": ["SECRET", "SOC2"],
    "jwt": ["SECRET", "SOC2"],
}


# Category labels (e.g. "Social Security Numbers") are not literal PII.
_PII_CATEGORY_LABEL_RE = re.compile(
    r"\b(?:social security numbers?|email addresses?|phone numbers?|"
    r"credit card numbers?|personally identifiable information)\b",
    re.IGNORECASE,
)

_SAFETY_REFUSAL_SIGNALS = (
    re.compile(r"(?:don't|do not)\s+share", re.IGNORECASE),
    re.compile(r"security advice", re.IGNORECASE),
    re.compile(r"(?:I don't|I do not)\s+(?:process|store|retain)\s+.*personal", re.IGNORECASE),
    re.compile(r"(?:cannot|can't)\s+(?:verify|store|process)", re.IGNORECASE),
    re.compile(r"not a (?:secure|verified)\s+(?:system|platform)", re.IGNORECASE),
    re.compile(r"aadhaar|aadhar", re.IGNORECASE),
)


def is_safety_refusal_output(text: str) -> bool:
    """True when output is benign security/privacy refusal advice (not factual hallucination)."""
    if not text or len(text) < 40:
        return False
    hits = sum(1 for pattern in _SAFETY_REFUSAL_SIGNALS if pattern.search(text))
    return hits >= 2


def _detect_pii_core(text: str) -> Dict[str, str]:
    """Raw PII/PHI/PCI detection over one text form (no canonicalization)."""
    found: Dict[str, str] = {}
    for group in (PII_PATTERNS, PHI_PATTERNS, PCI_PATTERNS):
        for pii_type, pattern_str in group.items():
            compiled = compile_pattern(pattern_str)
            match = compiled.search(text)
            if match:
                found.setdefault(pii_type, match.group(0))
    return found


def detect_pii(text: str) -> Dict[str, str]:
    """Detect PII/PHI/PCI, resistant to unicode/zero-width/homoglyph (G1) and base64/hex (G2)
    obfuscation. Matches on the raw text AND its canonical form AND bounded transport decodes,
    then filters label-only false positives. Canonical/decode passes are skipped when they add
    nothing (plain ASCII), so plain-text behaviour is unchanged."""
    found = _detect_pii_core(text)
    canon = canonicalize_for_detection(text)
    if canon != text:
        for k, v in _detect_pii_core(canon).items():
            found.setdefault(k, v)
    for dec in _iter_transport_decodes_canon(text, canon):
        # G26: the decoded payload may itself be unicode-obfuscated (base64 ∘ zero-width /
        # tags), so match its CANONICAL form too, not just the raw decode. G75: the decode set
        # now also covers the canonical text, so a Cf-split (e.g. ALM-in-blob) base64 is decoded.
        dec_canon = canonicalize_for_detection(dec)
        for src in ((dec, dec_canon) if dec_canon != dec else (dec,)):
            for k, v in _detect_pii_core(src).items():
                found.setdefault(k, v)
    return _filter_pii_label_false_positives(text, found)


def _filter_pii_label_false_positives(text: str, found: Dict[str, str]) -> Dict[str, str]:
    """Drop matches that are only category-label discussion without literal values."""
    if not found:
        return found
    # If the only "hits" are near category headings in a refusal list, require literals.
    label_only_context = bool(_PII_CATEGORY_LABEL_RE.search(text))
    if not label_only_context:
        return found
    literal_types = {"ssn", "credit_card", "email", "phone_us", "iban_code", "us_bank_number"}
    filtered = dict(found)
    for pii_type in list(filtered.keys()):
        if pii_type not in literal_types:
            continue
        span = filtered[pii_type]
        # Keep literal SSN/CC/email/phone shapes; drop if span is just a label word.
        if pii_type == "ssn" and not re.search(r"\d{3}-\d{2}-\d{4}", span):
            del filtered[pii_type]
        elif pii_type == "credit_card" and not re.search(r"\d{4}", span):
            del filtered[pii_type]
        elif pii_type == "email" and "@" not in span:
            del filtered[pii_type]
        elif pii_type == "phone_us" and not re.search(r"\d{3}", span):
            del filtered[pii_type]
    return filtered


def _detect_secrets_core(text: str) -> Dict[str, str]:
    """Raw secret detection over one text form (no canonicalization)."""
    found: Dict[str, str] = {}
    for secret_type, pattern_str in SECRET_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found.setdefault(secret_type, match.group(0))
    return found


def detect_secrets(text: str) -> Dict[str, str]:
    """Detect secrets, resistant to unicode/zero-width/homoglyph (G1) and base64/hex (G2)
    obfuscation. Matches on the raw text AND its canonical form AND bounded transport decodes.
    Canonical/decode passes are skipped when they add nothing (plain ASCII)."""
    found = _detect_secrets_core(text)
    canon = canonicalize_for_detection(text)
    if canon != text:
        for k, v in _detect_secrets_core(canon).items():
            found.setdefault(k, v)
    for dec in _iter_transport_decodes_canon(text, canon):   # G75: raw + canonical decode set
        dec_canon = canonicalize_for_detection(dec)   # G26: canonicalize obfuscated decode
        for src in ((dec, dec_canon) if dec_canon != dec else (dec,)):
            for k, v in _detect_secrets_core(src).items():
                found.setdefault(k, v)
    return found


# ── Smart partial-masking helpers ──────────────────────────────────────────────

def _mask_email(m: re.Match) -> str:
    """john@company.com → j***@c***.com"""
    full = m.group(0)
    try:
        local, domain_full = full.split("@", 1)
        domain_parts = domain_full.rsplit(".", 1)
        tld = domain_parts[1] if len(domain_parts) > 1 else ""
        domain_name = domain_parts[0]
        masked_local = (local[0] + "***") if local else "***"
        masked_domain = (domain_name[0] + "***") if domain_name else "***"
        return f"{masked_local}@{masked_domain}.{tld}"
    except Exception:
        return "[EMAIL]"


def _mask_credit_card(m: re.Match) -> str:
    """4111-1111-1111-1111 → ****-****-****-1111"""
    digits = re.sub(r"[\s\-]", "", m.group(0))
    return f"****-****-****-{digits[-4:]}"


def _mask_ssn(m: re.Match) -> str:
    """123-45-6789 → ***-**-6789"""
    raw = m.group(0)
    return f"***-**-{raw[-4:]}"


def _mask_phone(m: re.Match) -> str:
    """555-867-5309 / +44 20 7946 0958 / 415.555.0142 → ***-***-<last4>"""
    digits = re.sub(r"[^\d]", "", m.group(0))
    return f"***-***-{digits[-4:]}"


def _mask_ssn_digits(m: re.Match) -> str:
    """123 45 6789 → ***-**-6789 (separators-only SSN; keep last 4 digits)."""
    digits = re.sub(r"\D", "", m.group(0))
    return f"***-**-{digits[-4:]}"


def _mask_ssn_nosep(m: re.Match) -> str:
    """'ssn 123456789' → 'ssn ***-**-6789' — preserve the keyword context,
    mask only the trailing 9-digit value so surrounding prose is untouched."""
    return re.sub(
        r"\d{9}\b",
        lambda d: "***-**-" + d.group(0)[-4:],
        m.group(0),
    )


def _mask_api_key(m: re.Match) -> str:
    """sk-abc...xyz → sk-****xyz"""
    s = m.group(0)
    dash_idx = s.find("-")
    prefix = s[: dash_idx + 1] if dash_idx != -1 else s[:3]
    return f"{prefix}****{s[-4:]}" if len(s) > len(prefix) + 8 else f"{prefix}****"


def _mask_aws_key(m: re.Match) -> str:
    """AKIA1234ABCD5678WXYZ → AKIA****WXYZ"""
    s = m.group(0)
    return f"AKIA****{s[-4:]}"


def _mask_aws_secret(m: re.Match) -> str:
    """AWS_SECRET_ACCESS_KEY=wJal...KEY → AWS_SECRET_ACCESS_KEY=*** (keep the
    variable name + separator, mask the secret value)."""
    raw = m.group(0)
    sep = re.search(r"[:=]\s*[\"']?", raw)
    return (raw[: sep.end()] + "***") if sep else "***"


def _mask_github_token(m: re.Match) -> str:
    """gh?_abc...xyz → gh?_****xyz (G93: preserve the actual 4-char prefix, not a hardcoded ghp_)"""
    s = m.group(0)
    return f"{s[:4]}****{s[-4:]}"


def _mask_secret_assignment(m: re.Match) -> str:
    """password="secret" → password="*** (keep key + separator, mask value)"""
    raw = m.group(0)
    sep_match = re.search(r'["\s:=]+', raw)
    if sep_match:
        return raw[: sep_match.end()] + "***"
    return raw[:4] + "***"



def _mask_phone_bare_contextual(m: re.Match) -> str:
    """Mask only the trailing 10-digit phone (contiguous OR separator-split) in a
    contextual phone phrase, preserving the cue prefix."""
    s = m.group(0)
    digits_match = compile_pattern(_BARE_PHONE_10_SPLIT + r"\b").search(s)
    if not digits_match:
        return s
    digits = re.sub(r"\D", "", digits_match.group(0))
    masked = f"***-***-{digits[-4:]}"
    return s[: digits_match.start()] + masked + s[digits_match.end() :]


_PII_MASKERS = {
    "email": _mask_email,
    "credit_card": _mask_credit_card,
    "ssn": _mask_ssn,
    "ssn_spaced": _mask_ssn_digits,
    "ssn_nosep": _mask_ssn_nosep,
    "phone_us": _mask_phone,
    "phone_intl": _mask_phone,
    "phone_dotted": _mask_phone,
    "phone_us_bare_contextual": _mask_phone_bare_contextual,
    "api_key_openai": _mask_api_key,
    "aws_access_key": _mask_aws_key,
    "aws_secret_access_key": _mask_aws_secret,
    "github_token": _mask_github_token,
    "private_key_header": lambda m: "[PRIVATE_KEY]",
}

_SECRET_MASKERS = {
    "password_assignment": _mask_secret_assignment,
    "secret_assignment": _mask_secret_assignment,
    "token_assignment": _mask_secret_assignment,
    "api_key_assignment": _mask_secret_assignment,
}

_CREDENTIAL_MASKERS = {
    "bearer_token": lambda m: "Bearer [BEARER_TOKEN_REDACTED]",
    "basic_auth": lambda m: "Basic [BASIC_AUTH_REDACTED]",
    "exposed_password": lambda m: "password=[PASSWORD_REDACTED]",
    "connection_string": lambda m: "[CONNECTION_STRING_REDACTED]",
    "private_key_block": lambda m: "[PRIVATE_KEY_REDACTED]",
}


def redact_evidence_digit_spans(text: str, evidence_sources: List[str]) -> str:
    """Mask digit runs echoed in Tier-2 guard evidence when regex patterns miss them."""
    if not text or not evidence_sources:
        return text
    result = text
    spans: set[str] = set()
    for src in evidence_sources:
        if not src:
            continue
        for match in re.finditer(r"\b\d{8,15}\b", str(src)):
            spans.add(match.group(0))
    for digits in sorted(spans, key=len, reverse=True):
        if digits in result:
            result = result.replace(digits, f"***-***-{digits[-4:]}")
    return result


def _redact_all_raw(text: str) -> str:
    """Redact PII with smart partial masking; PHI/PCI use placeholder tags (raw text only)."""
    result = text
    for pii_type, pattern_str in PII_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _PII_MASKERS.get(pii_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{pii_type.upper()}_REDACTED]", result)
    for phi_type, pattern_str in PHI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        result = compiled.sub(f"[{phi_type.upper()}_REDACTED]", result)
    for pci_type, pattern_str in PCI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        result = compiled.sub(f"[{pci_type.upper()}_REDACTED]", result)
    for secret_type, pattern_str in SECRET_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _SECRET_MASKERS.get(secret_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{secret_type.upper()}_REDACTED]", result)
    for cred_type, pattern_str in CREDENTIAL_EXPOSURE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _CREDENTIAL_MASKERS.get(cred_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{cred_type.upper()}_REDACTED]", result)
    # E15: internal infrastructure leakage — surgically mask a REAL internal
    # NETWORK address (private IPv4 / internal hostname / internal URL) so it can
    # never egress raw on the client channel, matching PII's always-redact-when-
    # detected behaviour. Canonical example/gateway addresses (the textbook
    # 192.168.0.1) are exempt so benign educational answers aren't degraded. File
    # paths are intentionally NOT masked here — they are far too false-positive-
    # prone (legitimate in code answers) and stay at the softer 'flag' tier.
    # CHG-0073: internal_ipv6 + link_local_ipv4 added so redact_all ACTUALLY masks
    # them. This tuple is hardcoded (not `IP_LEAKAGE_PATTERNS.keys()`), so a new IP
    # key detected by detect_ip_leakage but absent here would be FLAGGED-yet-
    # FORWARDED-RAW under a redact policy — the "report redacted while egressing
    # raw" fail-open. They carry no example-address exemption (169.254.169.254 /
    # ULA / link-local IPv6 must always mask), so no branch is added below.
    for _infra_type in (
        "internal_ipv4", "internal_hostname", "internal_url",
        "internal_ipv6", "link_local_ipv4",
    ):
        _infra_pat = IP_LEAKAGE_PATTERNS.get(_infra_type)
        if not _infra_pat:
            continue
        _infra_compiled = compile_pattern(_infra_pat)

        def _infra_sub(m, lt=_infra_type):
            val = m.group(0)
            if lt == "internal_ipv4" and val in _IP_LEAKAGE_EXAMPLE_ADDRS:
                return val
            if lt == "internal_url" and _ip_url_host(val) in _IP_LEAKAGE_EXAMPLE_ADDRS:
                return val
            return f"[{lt.upper()}_REDACTED]"

        result = _infra_compiled.sub(_infra_sub, result)
    return result


def _detect_all_spans(text: str):
    """Yield ``(label, start, end)`` for every PII/PHI/PCI/secret match in ``text``."""
    for group in (PII_PATTERNS, PHI_PATTERNS, PCI_PATTERNS, SECRET_PATTERNS):
        for label, pattern_str in group.items():
            compiled = compile_pattern(pattern_str)
            for m in compiled.finditer(text):
                if m.group(0):
                    yield label, m.start(), m.end()


# CHG-0058: an ENCODED (base64/hex/url) internal IP/host/URL bypassed the obfuscation
# pass — its decode checks only detect_pii/detect_secrets, NOT ip_leakage. Scope the
# encoded-infra check to the NETWORK-address keys redact_all actually masks (internal
# IP / hostname / URL); file-path leak types are excluded (flag-tier + FP-prone).
_INFRA_NETWORK_KEYS = (
    "internal_ipv4", "internal_hostname", "internal_url",
    "internal_ipv6", "link_local_ipv4",  # CHG-0073: encoded-infra parity
)


def _dec_has_infra(s: str) -> bool:
    """True if a DECODED obfuscated blob carries an internal network address."""
    return any(k in _INFRA_NETWORK_KEYS for k in detect_ip_leakage(s))


# CHG-0058: the shared base64 gate (_B64ISH_RE, {12,}) needs >=12 base64 chars, i.e. a
# decoded payload of ~>=9 bytes. A BARE short internal IPv4 whose string form is <=8 bytes
# (e.g. "10.1.2.3" -> "MTAuMS4yLjM=", 11 base64 chars) falls just under the gate, so a lone
# short internal IP could egress base64-encoded and be trivially recovered. This dedicated
# SHORT-token pass (a maximal run of 8..11 base64 chars, the band the main gate misses)
# decodes and masks ONLY when the result is an internal NETWORK address (specific, low-FP)
# -- it deliberately does NOT run the full PII/secret suite, so it changes nothing about
# detect_pii/detect_secrets and only tightens the fail-closed redaction path. The leading
# look-behind + trailing look-ahead pin it to maximal runs, so 12+ char tokens (already
# handled by the main pass) are never partially re-matched. Token count is bounded.
_SHORT_B64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{8,11}={0,2}(?![A-Za-z0-9+/])")


def _iter_short_b64_infra(text: str):
    """Yield ``(token, decoded)`` for SHORT base64 tokens (8..11 chars, below the main
    _B64ISH_RE gate) whose decode is an internal network address. Bounded + network-key-
    only => low FP, decode-bomb safe."""
    if not text:
        return
    scan = text[:_CANON_MAX_LEN]
    seen = 0
    for m in _SHORT_B64_RE.finditer(scan):
        if seen >= _MAX_DECODE_TOKENS:
            break
        seen += 1
        tok = m.group(0)
        dec = _decode_one(tok, False)
        if dec is not None and _dec_has_infra(dec):
            yield tok, dec


def _detect_infra_cred_spans(text: str):
    """Yield ``(label, start, end)`` for internal-network (IP/host/URL) and credential-exposure
    matches — the two categories :func:`_detect_all_spans` omits. Used ONLY on the canonical
    form inside :func:`_redact_obfuscated` (G54) so an obfuscation-revealed internal address or
    credential is masked back onto the ORIGINAL bytes instead of being flagged-yet-egressed-raw
    (the report-redacted-while-forwarding-raw fail-open). Scoped to the network infra keys
    ``redact_all`` already masks on the raw pass (file paths excluded — flag-tier + FP-prone),
    with the same example-address carve-out; all credential keys are masked (no benign-address
    exemption applies to a credential)."""
    for label in _INFRA_NETWORK_KEYS:
        pattern_str = IP_LEAKAGE_PATTERNS.get(label)
        if not pattern_str:
            continue
        compiled = compile_pattern(pattern_str)
        for m in compiled.finditer(text):
            val = m.group(0)
            if not val:
                continue
            if label == "internal_ipv4" and val in _IP_LEAKAGE_EXAMPLE_ADDRS:
                continue
            if label == "internal_url" and _ip_url_host(val) in _IP_LEAKAGE_EXAMPLE_ADDRS:
                continue
            yield label, m.start(), m.end()
    for label, pattern_str in CREDENTIAL_EXPOSURE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        for m in compiled.finditer(text):
            if m.group(0):
                yield label, m.start(), m.end()


def _redact_obfuscated(original: str, result: str) -> str:
    """Mask obfuscated PII/secret (G1) and encoded PII/secret blobs (G2) in ``result``.

    ``result`` is the raw-redacted text. Obfuscated spans the raw pass missed survive
    verbatim in ``result``, so they can be found and masked by exact substring. Masking
    the WHOLE original span with a placeholder is intentional (obfuscated PII gets fully
    removed). No-op when the input carries no obfuscation and no decodable blob."""
    masks: List[tuple[str, str]] = []
    canon, idx = _canonicalize_with_map(original)
    if canon and canon != original:
        for label, a, b in _detect_all_spans(canon):
            if 0 <= a < len(idx) and 0 <= b - 1 < len(idx):
                orig_sub = original[idx[a]: idx[b - 1] + 1]
                if orig_sub:
                    masks.append((orig_sub, f"[{label.upper()}_REDACTED]"))
        # G54: infra (internal IP/host/URL) + credential spans that ONLY the canonical form
        # reveals (fullwidth / zero-width / homoglyph). detect_ip_leakage /
        # detect_credential_exposure now flag these on the output guard, but _detect_all_spans
        # above covers only PII/PHI/PCI/SECRET — so without this the masked category would be
        # FLAGGED-yet-FORWARDED-RAW. Map each canonical match back onto the original bytes.
        for label, a, b in _detect_infra_cred_spans(canon):
            if 0 <= a < len(idx) and 0 <= b - 1 < len(idx):
                orig_sub = original[idx[a]: idx[b - 1] + 1]
                if orig_sub:
                    masks.append((orig_sub, f"[{label.upper()}_REDACTED]"))
        # G85: entity/percent-encoded AND markdown-emphasis-split PII/secret with Cf (zero-width/bidi/
        # format) interleaved through them. The output detectors + neutralizers decode/strip these over
        # RAW text, so Cf-interleave broke the run and the secret evaded BOTH detection (verdict allow ->
        # raw egress) AND masking. ``canon`` is the Cf-stripped view (whitespace PRESERVED — unlike a
        # base64 blob, an entity/percent/markdown value split by a SPACE renders WITH the space, so it is
        # a genuine boundary, not reassembled). Find each run in canon, decode/strip it, and if it reveals
        # PII/secret/infra/credential mask the mapped-back ORIGINAL span (spanning the interleaved Cf).
        def _reveals_secret(_s: str) -> bool:
            return bool(_detect_pii_core(_s) or _detect_secrets_core(_s)
                        or _dec_has_infra(_s) or _detect_credential_exposure_core(_s))

        def _mask_canon_span(_a: int, _b: int, _tag: str) -> None:
            if 0 <= _a < len(idx) and 0 <= _b - 1 < len(idx):
                _os = original[idx[_a]: idx[_b - 1] + 1]
                if _os:
                    masks.append((_os, _tag))

        for _m in _ENTITY_RUN_RE.finditer(canon):
            _d = _decode_entity_run(_m.group(0))
            if _d != _m.group(0) and _reveals_secret(_d):
                _mask_canon_span(_m.start(), _m.end(), "[ENCODED_SECRET_REDACTED]")
        for _m in _PCT_RUN_RE.finditer(canon):
            try:
                _d = urllib.parse.unquote(_m.group(0))
            except Exception:  # noqa: BLE001 - decode must never break redaction
                continue
            if _d != _m.group(0) and _reveals_secret(_d):
                _mask_canon_span(_m.start(), _m.end(), "[ENCODED_SECRET_REDACTED]")
        for _m in _MD_SPLIT_RUN_RE.finditer(canon):
            _st = _m.group(0).replace("*", "").replace("`", "")
            if _st != _m.group(0) and _reveals_secret(_st):
                _mask_canon_span(_m.start(), _m.end(), "[PII_REDACTED]")
    for tok, dec in _iter_transport_decodes(original):
        # G26: mask the OUTER encoded blob when its decode carries PII/secret in either
        # raw OR canonical (unicode-obfuscated, e.g. base64 ∘ zero-width) form.
        # G55: also when the decode is a CREDENTIAL-only value (connection string /
        # basic-auth / stripe/github/azure key — not in SECRET_PATTERNS), else a base64/hex-
        # encoded credential would be flagged by the guard yet egress un-masked here.
        dcanon = canonicalize_for_detection(dec)
        if (_detect_pii_core(dec) or _detect_secrets_core(dec) or _dec_has_infra(dec)
                or _detect_credential_exposure_core(dec)
                or (dcanon != dec and (_detect_pii_core(dcanon) or _detect_secrets_core(dcanon)
                                       or _dec_has_infra(dcanon)
                                       or _detect_credential_exposure_core(dcanon)))):
            masks.append((tok, "[ENCODED_SECRET_REDACTED]"))
    # G84: the raw transport loop above scans ``original`` directly, so a base64/hex blob whose
    # chars are interleaved with zero-width/bidi/format (Cf) OR split by ASCII whitespace does NOT
    # match a contiguous token and egressed UN-MASKED — even though DETECTION flags it (it decodes
    # over the Cf-stripped canonical AND whitespace-collapsed views via _iter_transport_decodes_canon,
    # G75/G76). That masker/detector asymmetry let a redact verdict fail OPEN (the egress path emits
    # the still-decodable blob under a "flag" relabel). Mirror the detection normalization here: decode
    # over a transport-normalized view (canonical Cf-stripped form with ASCII whitespace collapsed) and
    # map each secret-bearing token's span BACK onto the ORIGINAL bytes (WITH the interleaved chars) via
    # the ``idx`` map, exactly like the canonical-PII masking above — so the whole obfuscated blob is
    # removed. Decode-gated (only masks when the decode carries PII/secret/infra/credential), so a benign
    # whitespace-separated base64-charset run is untouched.
    if canon:
        _tnorm_chars: List[str] = []
        _tnorm_idx: List[int] = []
        for _k, _ch in enumerate(canon):
            if _ch in " \t\n\r\f\v":
                continue
            _tnorm_chars.append(_ch)
            _tnorm_idx.append(idx[_k])
        _tnorm = "".join(_tnorm_chars)
        if _tnorm and _tnorm != original:
            for tok, dec in _iter_transport_decodes(_tnorm):
                dcanon = canonicalize_for_detection(dec)
                if (_detect_pii_core(dec) or _detect_secrets_core(dec) or _dec_has_infra(dec)
                        or _detect_credential_exposure_core(dec)
                        or (dcanon != dec and (_detect_pii_core(dcanon) or _detect_secrets_core(dcanon)
                                               or _dec_has_infra(dcanon)
                                               or _detect_credential_exposure_core(dcanon)))):
                    _pos = _tnorm.find(tok)
                    if 0 <= _pos and _pos + len(tok) - 1 < len(_tnorm_idx):
                        _orig_sub = original[_tnorm_idx[_pos]: _tnorm_idx[_pos + len(tok) - 1] + 1]
                        if _orig_sub:
                            masks.append((_orig_sub, "[ENCODED_SECRET_REDACTED]"))
    # CHG-0058: short base64 tokens (8..11 chars) below the main gate carrying a bare
    # internal network address (e.g. base64("10.1.2.3")). Network-key-only, bounded.
    for tok, _dec in _iter_short_b64_infra(original):
        masks.append((tok, "[ENCODED_SECRET_REDACTED]"))
    # CHG-0056: percent/URL-encoding obfuscation — a %XX-encoded PII/secret (e.g. an
    # email in a URL query param) breaks the raw pattern but is trivially recoverable.
    # Decode tokens carrying a %XX and, if the decoded form matches PII/secret, mask the
    # whole encoded token. Only masks when decoded PII/secret is found, so benign
    # percent text ("50%20off", "C%3A%5Cpath") is untouched. Token count bounded.
    for tok in _PERCENT_TOKEN_RE.findall(original[:_CANON_MAX_LEN])[:_MAX_URL_DECODE_TOKENS]:
        try:
            dec = urllib.parse.unquote(tok)
        except Exception:
            continue
        if dec != tok and (_detect_pii_core(dec) or _detect_secrets_core(dec)
                           or _dec_has_infra(dec) or _detect_credential_exposure_core(dec)):
            masks.append((tok, "[ENCODED_SECRET_REDACTED]"))
    for sub, tag in sorted(masks, key=lambda x: -len(x[0])):
        if sub and sub in result:
            result = result.replace(sub, tag)
    return result


def redact_all(text: str) -> str:
    """Redact PII/secrets from ``text``, resistant to unicode/zero-width/homoglyph (G1) and
    base64/hex (G2) obfuscation. Runs the raw partial-masking pass, then masks any obfuscated
    or encoded PII/secret that survived. A no-op beyond the raw pass on plain ASCII, so the
    frozen golden cases and existing redaction outputs are unchanged."""
    result = _redact_all_raw(text)
    return _redact_obfuscated(text, result)


def get_compliance_tags(pattern_keys: List[str]) -> List[str]:
    """
    Given a list of matched pattern keys (e.g. ['ssn', 'credit_card']),
    return deduplicated compliance framework tags.
    """
    tags: set[str] = set()
    for key in pattern_keys:
        mapped = COMPLIANCE_TAG_MAP.get(key)
        if mapped:
            tags.update(mapped)
    return sorted(tags)


def detect_hallucination_markers(text: str) -> Dict[str, str]:
    """Detect hallucination risk markers in text."""
    if is_safety_refusal_output(text):
        return {}
    found: Dict[str, str] = {}
    for marker_type, pattern_str in HALLUCINATION_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[marker_type] = match.group(0)
    return found


# R2: canonical example / default-gateway / network addresses that appear in
# documentation, tutorials, and teaching material. Flagging these as
# "infrastructure leakage" is a false positive (the textbook home-router
# example 192.168.0.1 is not a real internal-infra disclosure). Mirrors the
# file_path doc-path carve-out above. An ARBITRARY internal IP (e.g.
# 192.168.50.123) still flags — only the well-known example/gateway addresses
# are exempt.
_IP_LEAKAGE_EXAMPLE_ADDRS: frozenset = frozenset({
    "192.168.0.1", "192.168.1.1", "192.168.0.0", "192.168.1.0",
    "10.0.0.1", "10.0.0.0", "172.16.0.1", "172.16.0.0",
})


def _ip_url_host(value: str) -> str:
    """Strip scheme + trailing punctuation from an internal_url match to get the host."""
    host = re.sub(r"^https?://", "", value)
    return host.rstrip(":/").split("/")[0].split(":")[0]


def _detect_ip_leakage_core(text: str) -> Dict[str, str]:
    """Raw internal IP/infrastructure leakage detection over one text form (no canon).

    R2: skips canonical example/default-gateway addresses so a benign textbook
    example doesn't destroy the response. Uses ``finditer`` (not ``search``) so
    that a REAL internal IP appearing alongside an example address is still
    detected — only an output whose sole match is an example address is exempt.
    """
    found: Dict[str, str] = {}
    for leak_type, pattern_str in IP_LEAKAGE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        for match in compiled.finditer(text):
            value = match.group(0)
            if leak_type == "internal_ipv4" and value in _IP_LEAKAGE_EXAMPLE_ADDRS:
                continue
            if leak_type == "internal_url" and _ip_url_host(value) in _IP_LEAKAGE_EXAMPLE_ADDRS:
                continue
            found[leak_type] = value
            break
    return found


def detect_ip_leakage(text: str) -> Dict[str, str]:
    """Detect internal IP/infrastructure leakage, resistant to unicode/zero-width/homoglyph
    obfuscation (G54). Matches the raw text AND its canonical form — so a fullwidth /
    zero-width-split / homoglyph internal address on OUTPUT (e.g. ``１０.２０.３０.４０``) can no
    longer evade the raw regex the way it did detect_pii/detect_secrets before G1. The
    canonical pass is skipped when it adds nothing (plain ASCII), so plain-text behaviour and
    the frozen suite are unchanged.

    G55: also matches bounded base64/hex TRANSPORT decodes, so an internal address emitted
    base64/hex-encoded on OUTPUT (``aG9zdCBpcyAxMC4yMC4zMC40MA==``) is flagged at the guard
    (previously only ``redact_all``'s _dec_has_infra masked it — moot when detection missed
    and the verdict stayed ``allow`` so the blob egressed raw). Mirrors detect_pii/detect_secrets."""
    found = _detect_ip_leakage_core(text)
    canon = canonicalize_for_detection(text)
    if canon != text:
        for k, v in _detect_ip_leakage_core(canon).items():
            found.setdefault(k, v)
    # G87: use the Cf-aware / whitespace-collapsed transport decode (parity with
    # detect_pii/detect_secrets, G75/G76). The raw _iter_transport_decodes broke on a
    # base64/hex blob with zero-width/bidi/format chars interleaved, so a Cf-obfuscated
    # base64-encoded internal IP evaded the guard (verdict allow -> the blob egressed raw).
    for dec in _iter_transport_decodes_canon(text, canon):
        dec_canon = canonicalize_for_detection(dec)
        for src in ((dec, dec_canon) if dec_canon != dec else (dec,)):
            for k, v in _detect_ip_leakage_core(src).items():
                found.setdefault(k, v)
    return found


def _detect_credential_exposure_core(text: str) -> Dict[str, str]:
    """Raw credential-exposure detection over one text form (no canonicalization)."""
    found: Dict[str, str] = {}
    for cred_type, pattern_str in CREDENTIAL_EXPOSURE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[cred_type] = match.group(0)
    return found


def detect_credential_exposure(text: str) -> Dict[str, str]:
    """Detect credential exposure, resistant to unicode/zero-width/homoglyph obfuscation
    (G54). Matches the raw text AND its canonical form, so a fullwidth / zero-width-split /
    homoglyph credential on OUTPUT (a connection string, basic-auth blob, stripe/github/azure
    key the raw regex missed) no longer evades the check — mirrors detect_pii/detect_secrets.
    Canonical pass skipped on plain ASCII (no behaviour change for plain text).

    G55: also matches bounded base64/hex TRANSPORT decodes. The credential-only patterns
    (connection string, basic-auth, stripe/github/azure key) are NOT in SECRET_PATTERNS, so a
    base64/hex-encoded credential on OUTPUT decoded to none of detect_pii/detect_secrets and
    evaded the whole output guard (verdict allow -> encoded blob egressed raw -> client decodes
    it). This decode pass closes that gap the same way detect_pii/detect_secrets already do."""
    found = _detect_credential_exposure_core(text)
    canon = canonicalize_for_detection(text)
    if canon != text:
        for k, v in _detect_credential_exposure_core(canon).items():
            found.setdefault(k, v)
    for dec in _iter_transport_decodes_canon(text, canon):   # G75: raw + canonical decode set
        dec_canon = canonicalize_for_detection(dec)
        for src in ((dec, dec_canon) if dec_canon != dec else (dec,)):
            for k, v in _detect_credential_exposure_core(src).items():
                found.setdefault(k, v)
    return found

"""FROZEN oracle: the original GW09 deterministic matcher, ruleset and canonicaliser.

v2-enforcement-leaks task 10 (design Property 2, Property 13; clauses 3.6-3.9). This is the
"unfixed function" the detection-monotonicity preservation test compares the shipped
detector against. It is a byte-for-byte logic copy, taken on the UNFIXED tree, of:

* ``gateway_v2/detect/deterministic/patterns.py`` -- every ``RULESET`` entry in declaration
  order: name, category, kind, prefilter, precise and detector name;
* ``gateway_v2/detect/deterministic/matcher.py`` -- the one combined-alternation
  ``finditer`` whose hits are confirmed by the precise regex anchored at the hit start;
* ``gateway_v2/detect/deterministic/canonicalize.py`` -- the decode budget, the ordered
  decoder set, the ORIGINAL 20-entry ``_HOMOGLYPHS`` fold and the span map;
* ``gateway_v2/detect/deterministic/detectors.py`` -- the detector ids and the family filter.

Nothing here imports ``gateway_v2.detect``. The Group B and C fixes (failed-region rescans,
a Luhn validator, start-superset prefilters, a versioned confusable table) change those
modules; this file must NOT follow them. Do not edit it to track a fix: an edit makes the
preservation test compare the fix against itself.

Two read-outs go beyond the shipped surface. Both are pure functions of the original
ruleset over the original canonical text, so the test can classify a text without trusting
the code under test:

* :attr:`OracleScan.failed_regions` -- every primary prefilter hit whose precise confirm
  failed (the design's "failed-confirm region"), plus the unconfirmed tail of a confirmed
  hit whose prefilter ran past its precise match;
* :func:`lead_context_starts` -- every precise match that starts where its own prefilter
  does not match (the first disjunct of the design's ``C_C``: ``you are DAN``,
  ``enable developer mode``, ``answer without ... restrictions``, ``(415) 555-2671``).
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from gateway_v2.domain import Category, Span

__all__ = (
    "BY_NAME",
    "DETECTOR_IDS",
    "HOMOGLYPHS",
    "ORIGINAL_RULESET",
    "OracleBudgetExceeded",
    "OracleFinding",
    "OracleScan",
    "OriginalPattern",
    "PrimaryHit",
    "canonicalize",
    "findings",
    "lead_context_starts",
    "scan",
)


# =========================================================================== #
# patterns.py -- the original RULESET, in declaration order.
# =========================================================================== #


@dataclass(frozen=True, slots=True)
class OriginalPattern:
    """One original ``PatternSpec``; ``kind`` is the ``PatternKind`` value string."""

    name: str
    category: Category
    kind: str
    prefilter: str
    precise: str
    detector_name: str


_EMAIL_PREFILTER: Final = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+"
_EMAIL_PRECISE: Final = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"

_PHONE_PREFILTER: Final = r"(?:\+?\d[\s().\-]?){9,}\d"
_PHONE_PRECISE: Final = r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\d)"

_SSN_PREFILTER: Final = r"\d{3}[\s\-]\d{2}[\s\-]\d{4}"
_SSN_PRECISE: Final = r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"

_AWS_PREFILTER: Final = r"(?:AKIA|ASIA)[A-Z2-7]{10,}"
_AWS_PRECISE: Final = r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z2-7]{16}(?![A-Z0-9])"

_API_KEY_PREFILTER: Final = (
    r"(?i:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token"
    r"|client[_\-]?secret|bearer|token)[\"']?\s*[:=]?\s*[\"']?[A-Za-z0-9_\-./+=]{12,}"
)
_API_KEY_PRECISE: Final = (
    r"(?i:api[_\-]?key|secret[_\-]?key|access[_\-]?token|auth[_\-]?token"
    r"|client[_\-]?secret|bearer|token)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{16,}"
)

_PEM_PREFILTER: Final = r"-----BEGIN[A-Z ]*PRIVATE KEY"
_PEM_PRECISE: Final = r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----"

_CARD_PREFILTER: Final = r"(?:\d[ \-]?){12,18}\d"
_CARD_PRECISE: Final = r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)"

_OVERRIDE_PREFILTER: Final = (
    r"(?i:(?:ignore|disregard|forget|override)\b[^\n]{0,40}"
    r"(?:instruction|above|prompt|rule))"
)
_OVERRIDE_PRECISE: Final = (
    r"(?i:ignore\s+(?:all\s+)?previous\s+instructions"
    r"|disregard\s+(?:the\s+)?above"
    r"|ignore\s+(?:the\s+)?(?:above|prior)\s+(?:instructions?|prompt)"
    r"|forget\s+(?:all\s+)?(?:previous\s+)?instructions?"
    r"|override\s+(?:the\s+)?(?:system\s+)?prompt)"
)

_ROLE_PREFILTER: Final = r"(?i:you\s+are\s+now)\b"
_ROLE_PRECISE: Final = r"(?i:you\s+are\s+now\s+(?:a|an|the|in|free|dan|unrestricted|\w+))"

_JAILBREAK_PREFILTER: Final = (
    r"(?i:jailbreak|developer\s+mode|do\s+anything\s+now|\bDAN\b"
    r"|without\s+(?:any\s+)?restrictions)"
)
_JAILBREAK_PRECISE: Final = (
    r"(?i:jailbreak"
    r"|enable\s+developer\s+mode|developer\s+mode\s+enabled"
    r"|do\s+anything\s+now"
    r"|you\s+are\s+DAN\b"
    r"|answer\s+without\s+(?:any\s+)?restrictions)"
)

_SUBST_PREFILTER: Final = r"\$\([^)]"
_SUBST_PRECISE: Final = r"\$\([^)\n]+\)"

_RMRF_PREFILTER: Final = r"(?i:(?:;|&&|\|\|)\s*rm\s+-)"
_RMRF_PRECISE: Final = r"(?i:(?:;|&&|\|\|)\s*rm\s+-[rfRF]{1,2}\b)"

_PIPE_SH_PREFILTER: Final = r"\|\s*(?:ba|z|da)?sh\b"
_PIPE_SH_PRECISE: Final = r"\|\s*(?:ba|z|da)?sh\b(?:\s|$|;)"

_REDACTABLE: Final = "redactable"
_SIGNAL: Final = "signal"

#: The original ruleset, in the original declaration (= alternation) order.
ORIGINAL_RULESET: Final[tuple[OriginalPattern, ...]] = (
    OriginalPattern(
        "pii_email", Category.PII, _REDACTABLE, _EMAIL_PREFILTER, _EMAIL_PRECISE, "pii"
    ),
    OriginalPattern(
        "pii_phone", Category.PII, _REDACTABLE, _PHONE_PREFILTER, _PHONE_PRECISE, "pii"
    ),
    OriginalPattern("pii_ssn", Category.PII, _REDACTABLE, _SSN_PREFILTER, _SSN_PRECISE, "pii"),
    OriginalPattern(
        "secret_aws_key", Category.SECRET, _REDACTABLE, _AWS_PREFILTER, _AWS_PRECISE, "secret"
    ),
    OriginalPattern(
        "secret_api_key",
        Category.SECRET,
        _REDACTABLE,
        _API_KEY_PREFILTER,
        _API_KEY_PRECISE,
        "secret",
    ),
    OriginalPattern(
        "credential_pem_private_key",
        Category.CREDENTIAL,
        _REDACTABLE,
        _PEM_PREFILTER,
        _PEM_PRECISE,
        "secret",
    ),
    OriginalPattern(
        "credential_card",
        Category.CREDENTIAL,
        _REDACTABLE,
        _CARD_PREFILTER,
        _CARD_PRECISE,
        "secret",
    ),
    OriginalPattern(
        "injection_instruction_override",
        Category.PROMPT_INJECTION,
        _SIGNAL,
        _OVERRIDE_PREFILTER,
        _OVERRIDE_PRECISE,
        "injection",
    ),
    OriginalPattern(
        "injection_role_reassignment",
        Category.PROMPT_INJECTION,
        _SIGNAL,
        _ROLE_PREFILTER,
        _ROLE_PRECISE,
        "injection",
    ),
    OriginalPattern(
        "injection_jailbreak",
        Category.JAILBREAK,
        _SIGNAL,
        _JAILBREAK_PREFILTER,
        _JAILBREAK_PRECISE,
        "injection",
    ),
    OriginalPattern(
        "command_substitution",
        Category.COMMAND,
        _SIGNAL,
        _SUBST_PREFILTER,
        _SUBST_PRECISE,
        "injection",
    ),
    OriginalPattern(
        "command_rm_rf", Category.COMMAND, _SIGNAL, _RMRF_PREFILTER, _RMRF_PRECISE, "injection"
    ),
    OriginalPattern(
        "command_pipe_to_shell",
        Category.COMMAND,
        _SIGNAL,
        _PIPE_SH_PREFILTER,
        _PIPE_SH_PRECISE,
        "injection",
    ),
)

#: The original ruleset by pattern name.
BY_NAME: Final[Mapping[str, OriginalPattern]] = MappingProxyType(
    {p.name: p for p in ORIGINAL_RULESET}
)

#: ``detectors.py``: the ``Finding.detector`` id of each family.
DETECTOR_IDS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "pii": "deterministic.pii",
        "secret": "deterministic.secret",
        "injection": "deterministic.injection",
    }
)


# =========================================================================== #
# canonicalize.py -- budget, decoder set, ORIGINAL _HOMOGLYPHS, span map.
# =========================================================================== #

_MAX_INPUT_BYTES: Final = 262_144
_MAX_EXPANSION_RATIO: Final = 8.0
_MAX_PARTS: Final = 4_096

_PERCENT_TOKEN: Final = re.compile(r"%[0-9A-Fa-f]{2}")
_ENTITY_TOKEN: Final = re.compile(r"&(?:#x?[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]{1,31});")
_UESC_TOKEN: Final = re.compile(r"\\u[0-9A-Fa-f]{4}")
_B64_MIN: Final = 16
_B64_MAX: Final = 512
_B64_RUN: Final = re.compile(rf"[A-Za-z0-9+/]{{{_B64_MIN},{_B64_MAX}}}={{0,2}}")

#: The ORIGINAL 20-entry confusable fold. The Group C fix widens the shipped table; this
#: copy stays at 20 so the oracle keeps scanning the original canonical text.
HOMOGLYPHS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "\u0430": "a",  # CYRILLIC SMALL A
        "\u0435": "e",  # CYRILLIC SMALL IE
        "\u043e": "o",  # CYRILLIC SMALL O
        "\u0440": "p",  # CYRILLIC SMALL ER
        "\u0441": "c",  # CYRILLIC SMALL ES
        "\u0445": "x",  # CYRILLIC SMALL HA
        "\u0443": "y",  # CYRILLIC SMALL U
        "\u0456": "i",  # CYRILLIC SMALL BYELORUSSIAN-UKRAINIAN I
        "\u0458": "j",  # CYRILLIC SMALL JE
        "\u04bb": "h",  # CYRILLIC SMALL SHHA
        "\u0501": "d",  # CYRILLIC SMALL KOMI DE
        "\u0391": "A",  # GREEK CAPITAL ALPHA
        "\u039f": "O",  # GREEK CAPITAL OMICRON
        "\u03bf": "o",  # GREEK SMALL OMICRON
        "\u0410": "A",  # CYRILLIC CAPITAL A
        "\u0415": "E",  # CYRILLIC CAPITAL IE
        "\u041e": "O",  # CYRILLIC CAPITAL O
        "\u0420": "P",  # CYRILLIC CAPITAL ER
        "\u0421": "C",  # CYRILLIC CAPITAL ES
        "\u0425": "X",  # CYRILLIC CAPITAL HA
    }
)

_BIDI_CONTROLS: Final = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f"
)
_AT_WORD: Final = re.compile(r"\s+(?:at|AT|At)\s+")
_DOT_WORD: Final = re.compile(r"\s+(?:dot|DOT|Dot)\s+")

_Cell = tuple[str, int, int]
_Records = tuple[tuple[int, int, int], ...]


class OracleBudgetExceeded(Exception):
    """The original decode budget rejected the text (the shipped ``BudgetExceeded``)."""


def _within_budget(text: str) -> bool:
    length = len(text)
    if length > _MAX_INPUT_BYTES:
        return False
    parts = (
        len(_PERCENT_TOKEN.findall(text))
        + len(_ENTITY_TOKEN.findall(text))
        + len(_UESC_TOKEN.findall(text))
        + len(_B64_RUN.findall(text))
    )
    if parts > _MAX_PARTS:
        return False
    if length == 0:
        return True
    estimated = float(length) + 3.0 * float(parts)
    return (estimated / float(length)) <= _MAX_EXPANSION_RATIO


def _buf_text(buf: list[_Cell]) -> str:
    return "".join(cell[0] for cell in buf)


def _span_of(buf: list[_Cell], start: int, end: int) -> tuple[int, int]:
    if start >= end:
        if not buf:
            return 0, 0
        idx = min(max(start, 0), len(buf) - 1)
        return buf[idx][1], buf[idx][1]
    lo = min(buf[k][1] for k in range(start, end))
    hi = max(buf[k][2] for k in range(start, end))
    return lo, hi


def _rebuild(source: str, matches: list[tuple[int, int, str]], buf: list[_Cell]) -> list[_Cell]:
    out: list[_Cell] = []
    cursor = 0
    for start, end, decoded in matches:
        if start < cursor:
            continue
        out.extend(buf[cursor:start])
        orig_lo, orig_hi = _span_of(buf, start, end)
        for ch in decoded:
            out.append((ch, orig_lo, orig_hi))
        cursor = end
    out.extend(buf[cursor:])
    return out


def _decode_percent(buf: list[_Cell]) -> list[_Cell]:
    for _ in range(3):
        source = _buf_text(buf)
        matches: list[tuple[int, int, str]] = []
        for m in _PERCENT_TOKEN.finditer(source):
            try:
                decoded = bytes.fromhex(m.group()[1:]).decode("latin-1")
            except ValueError:
                continue
            matches.append((m.start(), m.end(), decoded))
        if not matches:
            break
        buf = _rebuild(source, matches, buf)
    return buf


def _decode_entities(buf: list[_Cell]) -> list[_Cell]:
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _ENTITY_TOKEN.finditer(source):
        decoded = html.unescape(m.group())
        if decoded != m.group():
            matches.append((m.start(), m.end(), decoded))
    return _rebuild(source, matches, buf) if matches else buf


def _decode_base64(buf: list[_Cell]) -> list[_Cell]:
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _B64_RUN.finditer(source):
        token = m.group()
        if len(token) % 4 != 0:
            continue
        try:
            raw = base64.b64decode(token, validate=True)
            decoded = raw.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if not decoded.isprintable():
            continue
        matches.append((m.start(), m.end(), decoded))
    return _rebuild(source, matches, buf) if matches else buf


def _decode_fullwidth(buf: list[_Cell]) -> list[_Cell]:
    out: list[_Cell] = []
    for ch, lo, hi in buf:
        folded = unicodedata.normalize("NFKC", ch)
        if folded == ch:
            out.append((ch, lo, hi))
            continue
        for fch in folded:
            out.append((fch, lo, hi))
    return out


def _is_default_ignorable(ch: str) -> bool:
    code = ord(ch)
    if ch in ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad", "\u180e"):
        return True
    if 0xFE00 <= code <= 0xFE0F:
        return True
    return 0x1D173 <= code <= 0x1D17A


def _strip_ignorable(buf: list[_Cell]) -> list[_Cell]:
    out: list[_Cell] = []
    for ch, lo, hi in buf:
        if ch in _BIDI_CONTROLS:
            continue
        if unicodedata.category(ch) == "Mn":
            continue
        if _is_default_ignorable(ch):
            continue
        out.append((ch, lo, hi))
    return out


def _decode_unicode_escapes(buf: list[_Cell]) -> list[_Cell]:
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _UESC_TOKEN.finditer(source):
        try:
            decoded = chr(int(m.group()[2:], 16))
        except ValueError:
            continue
        matches.append((m.start(), m.end(), decoded))
    return _rebuild(source, matches, buf) if matches else buf


def _fold_homoglyphs(buf: list[_Cell]) -> list[_Cell]:
    return [(HOMOGLYPHS.get(ch, ch), lo, hi) for ch, lo, hi in buf]


def _normalize_bidi(buf: list[_Cell]) -> list[_Cell]:
    return [cell for cell in buf if cell[0] not in _BIDI_CONTROLS]


def _deobfuscate_email(buf: list[_Cell]) -> list[_Cell]:
    source = _buf_text(buf)
    matches: list[tuple[int, int, str]] = []
    for m in _AT_WORD.finditer(source):
        matches.append((m.start(), m.end(), "@"))
    for m in _DOT_WORD.finditer(source):
        matches.append((m.start(), m.end(), "."))
    if not matches:
        return buf
    matches.sort(key=lambda t: t[0])
    return _rebuild(source, matches, buf)


def canonicalize(text: str) -> tuple[str, _Records]:
    """The ORIGINAL single pass: ``(canonical, records)``, records = ``(i, lo, hi)``.

    Budget pre-check first; raises :class:`OracleBudgetExceeded` before any decode.
    """
    if not _within_budget(text):
        raise OracleBudgetExceeded("decode budget exceeded before heavy canonicalization")
    buf: list[_Cell] = [(ch, i, i + 1) for i, ch in enumerate(text)]
    buf = _decode_percent(buf)
    buf = _decode_entities(buf)
    buf = _decode_base64(buf)
    buf = _decode_fullwidth(buf)
    buf = _strip_ignorable(buf)
    buf = _decode_unicode_escapes(buf)
    buf = _fold_homoglyphs(buf)
    buf = _normalize_bidi(buf)
    buf = _deobfuscate_email(buf)
    canonical = _buf_text(buf)
    return canonical, tuple((i, cell[1], cell[2]) for i, cell in enumerate(buf))


def _to_original(records: _Records, canonical_start: int, canonical_end: int) -> Span:
    """``SpanMap.to_original``: the minimal original Span covering a canonical range."""
    if not records:
        return Span(start=0, end=0)
    lo = canonical_start if canonical_start >= 0 else 0
    hi = canonical_end if canonical_end >= 0 else 0
    if hi < lo:
        hi = lo
    covering = [r for r in records if lo <= r[0] < hi]
    if not covering:
        anchor = min(records, key=lambda r: abs(r[0] - lo))
        return Span(start=anchor[1], end=anchor[1])
    return Span(start=min(r[1] for r in covering), end=max(r[2] for r in covering))


# =========================================================================== #
# matcher.py -- one combined finditer, precise confirm anchored at the hit start.
# =========================================================================== #

_COMBINED: Final[re.Pattern[str]] = re.compile(
    "|".join(f"(?P<{p.name}>{p.prefilter})" for p in ORIGINAL_RULESET)
)
_PRECISE: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {p.name: re.compile(p.precise) for p in ORIGINAL_RULESET}
)
_PREFILTER: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {p.name: re.compile(p.prefilter) for p in ORIGINAL_RULESET}
)


@dataclass(frozen=True, slots=True)
class PrimaryHit:
    """One primary-pass prefilter hit, and where its precise confirm ended (``None``: failed)."""

    pattern_name: str
    hit_start: int
    hit_end: int
    confirm_end: int | None

    @property
    def confirmed(self) -> bool:
        return self.confirm_end is not None


@dataclass(frozen=True, slots=True)
class OracleScan:
    """The original scan: confirmed ``(pattern, start, end)`` matches plus every hit."""

    matches: tuple[tuple[str, int, int], ...]
    hits: tuple[PrimaryHit, ...]

    @property
    def failed_regions(self) -> tuple[tuple[int, int], ...]:
        """Every ``[start, end)`` the primary pass consumed without a confirmed match.

        Two kinds. The design's failed-confirm region is a hit whose precise confirm failed.
        The second kind is the tail ``[confirm_end, hit_end)`` of a confirmed hit whose
        prefilter ran past its precise match. The same leftmost-first consumption hides a
        match there: ``863061\\n4111-1111-1111-1111`` is one greedy phone hit, confirmed as
        ``863061\\n4111``, and the card in the tail is never tried.
        """
        out: list[tuple[int, int]] = []
        for hit in self.hits:
            if hit.confirm_end is None:
                out.append((hit.hit_start, hit.hit_end))
            elif hit.confirm_end < hit.hit_end:
                out.append((hit.confirm_end, hit.hit_end))
        return tuple(out)


def scan(canonical_text: str) -> OracleScan:
    """The ORIGINAL ``matcher.scan`` over ``canonical_text``."""
    matches: list[tuple[str, int, int]] = []
    hits: list[PrimaryHit] = []
    for match in _COMBINED.finditer(canonical_text):
        name = match.lastgroup
        if name is None:
            continue
        confirmed = _PRECISE[name].match(canonical_text, match.start())
        confirm_end = None if confirmed is None else confirmed.end()
        hits.append(PrimaryHit(name, match.start(), match.end(), confirm_end))
        if confirmed is None:
            continue
        matches.append((name, confirmed.start(), confirmed.end()))
    return OracleScan(matches=tuple(matches), hits=tuple(hits))


def lead_context_starts(canonical_text: str) -> tuple[tuple[str, int], ...]:
    """Every ``(pattern, start)`` where a precise match starts outside its prefilter.

    One ``finditer`` per original precise regex. A start where the pattern's own prefilter
    does not match is a place a start-superset prefilter (the Group C fix) can confirm
    something the original matcher never could.
    """
    out: list[tuple[str, int]] = []
    for pattern in ORIGINAL_RULESET:
        prefilter = _PREFILTER[pattern.name]
        for m in _PRECISE[pattern.name].finditer(canonical_text):
            if prefilter.match(canonical_text, m.start()) is None:
                out.append((pattern.name, m.start()))
    return tuple(out)


# =========================================================================== #
# detectors.py -- canonicalize once, scan once, map spans back to original bytes.
# =========================================================================== #


@dataclass(frozen=True, slots=True)
class OracleFinding:
    """One original EXECUTED finding, in canonical and in original-byte coordinates."""

    pattern_name: str
    detector: str
    category: Category
    canonical_start: int
    canonical_end: int
    span: Span


def findings(text: str) -> tuple[str, OracleScan, tuple[OracleFinding, ...]]:
    """``(canonical, scan, findings)`` of the original pipeline over ``text``.

    Raises :class:`OracleBudgetExceeded` where the original detectors emitted UNAVAILABLE.
    """
    canonical, records = canonicalize(text)
    result = scan(canonical)
    out: list[OracleFinding] = []
    for name, start, end in result.matches:
        pattern = BY_NAME[name]
        out.append(
            OracleFinding(
                pattern_name=name,
                detector=DETECTOR_IDS[pattern.detector_name],
                category=pattern.category,
                canonical_start=start,
                canonical_end=end,
                span=_to_original(records, start, end),
            )
        )
    return canonical, result, tuple(out)

"""Translate a Python `re` pattern into a `regex` pattern that matches exactly what `re` matches.

The pattern is parsed by `re`'s own parser, so escapes, sets, verbose mode, scoped flags and
quantifier syntax mean what `re` says they mean. The tree is re-emitted for `regex`, replacing
every construct whose meaning differs between the engines:

* \\d \\w \\s and negations: property classes equal to `re`'s, minus the code points the newer
  Unicode database in `regex` assigns but this interpreter's does not (computed at import);
* \\b \\B: lookarounds over that \\w; \\B is false on the empty string, as in `re`;
* case-insensitive literals and sets: `re`'s own verdict on every cased code point is taken,
  so its simple case folding and its special fixes (i/ı/İ, k/K, s/ſ, ...) are kept exactly;
* `.`, `^`, `$`: emitted with explicit scoped flags, so no global flag reaches `regex`.

Refused: the ASCII and LOCALE flags, and backreferences under IGNORECASE.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache

import regex
from re import _constants as C
from re import _parser as P

__all__ = ("Refused", "translate")


class Refused(ValueError):
    """A valid `re` pattern this translator will not serve (its meaning is not reproduced)."""


def _ranges(points):
    out = []
    start = prev = None
    for cp in points:
        if start is None:
            start = prev = cp
        elif cp == prev + 1:
            prev = cp
        else:
            out.append((start, prev))
            start = prev = cp
    if start is not None:
        out.append((start, prev))
    return out


def _esc(cp: int) -> str:
    if cp > 0xFFFF:
        return f"\\U{cp:08x}"
    if cp > 0xFF:
        return f"\\u{cp:04x}"
    return f"\\x{cp:02x}"


def _body(points) -> str:
    return "".join(_esc(a) if a == b else f"{_esc(a)}-{_esc(b)}" for a, b in _ranges(sorted(points)))


def _diff(re_pat: str, rx_pat: str) -> tuple[list[int], list[int]]:
    a, b = re.compile(re_pat), regex.compile(rx_pat)
    only_re, only_rx = [], []
    for cp in range(sys.maxunicode + 1):
        ch = chr(cp)
        x, y = a.match(ch) is not None, b.match(ch) is not None
        if x and not y:
            only_re.append(cp)
        elif y and not x:
            only_rx.append(cp)
    return only_re, only_rx


_WCLS = "[\\p{L}\\p{N}_]"
_W = _WCLS
_NW = "[^\\p{L}\\p{N}_]"
_D = "\\p{Nd}"
_ND = "\\P{Nd}"
_SPACE = [cp for cp in range(0x3001) if re.match(r"\s", chr(cp))]  # all of re's \s is below U+3001
_S = "[" + _body(_SPACE) + "]"
_NS = "[^" + _body(_SPACE) + "]"
_POS = {C.CATEGORY_DIGIT: _D, C.CATEGORY_WORD: _W, C.CATEGORY_SPACE: _S,
        C.CATEGORY_NOT_DIGIT: _ND, C.CATEGORY_NOT_WORD: _NW, C.CATEGORY_NOT_SPACE: _NS}
_COMPLEMENT = {_W: _NW, _NW: _W, _D: _ND, _ND: _D, _S: _NS, _NS: _S}
_BOUNDARY = f"(?:(?<={_W})(?!{_W})|(?<!{_W})(?={_W}))"
_NON_BOUNDARY = f"(?:(?<={_W})(?={_W})|(?<!{_W})(?!{_W})(?!\\A\\Z))"

# Code points whose case-insensitive match can differ from a plain code-point test.
_CASED = sorted(
    {cp for cp in range(sys.maxunicode + 1)
     if chr(cp).lower() != chr(cp) or chr(cp).upper() != chr(cp) or chr(cp).casefold() != chr(cp)}
    | {ord(c) for cp in range(sys.maxunicode + 1) for c in (chr(cp).lower(), chr(cp).upper())
       if len(c) == 1 and c != chr(cp)}
    | {cp for k, vs in getattr(re._compiler, "_ignorecase_fixes", {}).items() for cp in (k, *vs)}
)


def _set_source(items) -> str:
    """The set as `re` source (categories as escapes), for asking `re` its own verdict."""
    out = []
    for op, av in items:
        if op is C.NEGATE:
            continue
        if op is C.LITERAL:
            out.append(_esc(av))
        elif op is C.RANGE:
            out.append(f"{_esc(av[0])}-{_esc(av[1])}")
        elif op is C.CATEGORY:
            out.append({C.CATEGORY_DIGIT: r"\d", C.CATEGORY_NOT_DIGIT: r"\D",
                        C.CATEGORY_WORD: r"\w", C.CATEGORY_NOT_WORD: r"\W",
                        C.CATEGORY_SPACE: r"\s", C.CATEGORY_NOT_SPACE: r"\S"}[av])
        else:
            raise Refused(f"unsupported set item {op}")
    neg = any(op is C.NEGATE for op, _ in items)
    return "[" + ("^" if neg else "") + "".join(out) + "]"


def _parts(items):
    neg = False
    plain, cats = [], []
    for op, av in items:
        if op is C.NEGATE:
            neg = True
        elif op is C.LITERAL:
            plain.append(_esc(av))
        elif op is C.RANGE:
            plain.append(f"{_esc(av[0])}-{_esc(av[1])}")
        elif op is C.CATEGORY:
            cats.append(_POS[av])
        else:
            raise Refused(f"unsupported set item {op}")
    return neg, "".join(plain), tuple(cats)


def _build(neg: bool, plain: str, cats: tuple, icase: bool) -> str:
    """Set text. Categories never fold case (as in `re`); plain members fold when `icase`."""
    # No global flag is ever emitted, so a case-sensitive set needs no scope of its own.
    def wrap(text: str) -> str:
        return f"(?i:{text})" if icase else text
    if not cats:
        return wrap(f"[{'^' if neg else ''}{plain}]")
    if not plain and len(cats) == 1 and not neg:
        return cats[0]
    if not plain and len(cats) == 1 and cats[0] in _COMPLEMENT:
        return _COMPLEMENT[cats[0]]
    alts = ([wrap(f"[{plain}]")] if plain else []) + list(cats)
    member = "(?:" + "|".join(alts) + ")"
    return f"(?:(?!{member})(?s:.))" if neg else member


def _cs_set(items) -> str:
    return _build(*_parts(items), icase=False)


@lru_cache(maxsize=4096)
def _ci_set(source: str, neg: bool, plain: str, cats: tuple) -> str:
    """Case-insensitive set or literal with `re`'s exact verdict on every cased code point.

    Native `regex` case folding is kept wherever it agrees with `re` (the common case); the
    code points where it does not are added or removed explicitly.
    """
    ci = re.compile("(?i)" + source)
    native = _build(neg, plain, cats, icase=True)
    rx = regex.compile(native)
    add = [cp for cp in _CASED if ci.fullmatch(chr(cp)) and not rx.fullmatch(chr(cp))]
    drop = [cp for cp in _CASED if rx.fullmatch(chr(cp)) and not ci.fullmatch(chr(cp))]
    core = native
    if drop:
        core = f"(?![{_body(drop)}]){core}"
    if add:
        core = f"(?:[{_body(add)}]|{core})"
    return core


_ATOM = regex.compile(r"(?:\\[xuU][0-9a-f]+|\\[pP]\{\w+\}|\[(?:\\.|[^\\\]\[])*\])")


def _atomic(text: str) -> bool:
    """One char, one escape, one property or one bracket set: safe to quantify unwrapped."""
    return len(text) == 1 or _ATOM.fullmatch(text) is not None


def _emit(sub, flags: int, out: list[str]) -> None:
    for op, av in sub:
        _emit_one(op, av, flags, out)


def _emit_one(op, av, flags: int, out: list[str]) -> None:
    icase = bool(flags & C.SRE_FLAG_IGNORECASE)
    if op is C.LITERAL or op is C.NOT_LITERAL:
        items = [(C.LITERAL, av)] if op is C.LITERAL else [(C.NEGATE, None), (C.LITERAL, av)]
        if icase:
            out.append(_ci_set(_set_source(items), *_parts(items)))
        elif op is C.LITERAL:
            out.append(_esc(av))
        else:
            out.append(f"[^{_esc(av)}]")
    elif op is C.ANY:
        out.append("(?s:.)" if flags & C.SRE_FLAG_DOTALL else "(?-s:.)")
    elif op is C.IN:
        out.append(_ci_set(_set_source(av), *_parts(av)) if icase else _cs_set(av))
    elif op is C.BRANCH:
        out.append("(?:")
        for i, alt in enumerate(av[1]):
            if i:
                out.append("|")
            _emit(alt, flags, out)
        out.append(")")
    elif op is C.SUBPATTERN:
        group, add, dele, p = av
        if (add | dele) & (C.SRE_FLAG_ASCII | C.SRE_FLAG_LOCALE):
            raise Refused("ASCII and LOCALE flags are not supported")
        inner = (flags | add) & ~dele
        out.append("(?:" if group is None else "(")
        _emit(p, inner, out)
        out.append(")")
    elif op in (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT):
        lo, hi, item = av
        inner: list[str] = []
        _emit(item, flags, inner)
        text = "".join(inner)
        out.append(text if _atomic(text) else f"(?:{text})")
        q = {(0, C.MAXREPEAT): "*", (1, C.MAXREPEAT): "+", (0, 1): "?"}.get((lo, hi))
        out.append(q or "{%d,%s}" % (lo, "" if hi is C.MAXREPEAT else hi))
        if op is C.MIN_REPEAT:
            out.append("?")
        elif op is C.POSSESSIVE_REPEAT:
            out.append("+")
    elif op is C.ATOMIC_GROUP:
        out.append("(?>")
        _emit(av, flags, out)
        out.append(")")
    elif op is C.AT:
        multiline = bool(flags & C.SRE_FLAG_MULTILINE)
        out.append({
            C.AT_BEGINNING: "(?m:^)" if multiline else "(?-m:^)",
            C.AT_END: "(?m:$)" if multiline else "(?-m:$)",
            C.AT_BEGINNING_STRING: "\\A",
            C.AT_END_STRING: "\\Z",
            C.AT_BOUNDARY: _BOUNDARY,
            C.AT_NON_BOUNDARY: _NON_BOUNDARY,
        }[av])
    elif op in (C.ASSERT, C.ASSERT_NOT):
        direction, p = av
        out.append({(C.ASSERT, 1): "(?=", (C.ASSERT, -1): "(?<=",
                    (C.ASSERT_NOT, 1): "(?!", (C.ASSERT_NOT, -1): "(?<!"}[(op, direction)])
        _emit(p, flags, out)
        out.append(")")
    elif op is C.GROUPREF:
        if icase:
            raise Refused("a backreference under IGNORECASE is not supported")
        out.append(f"(?:\\g<{av}>)")
    elif op is C.GROUPREF_EXISTS:
        group, yes, no = av
        out.append(f"(?({group})")
        _emit(yes, flags, out)
        if no is not None:
            out.append("|")
            _emit(no, flags, out)
        out.append(")")
    else:
        raise Refused(f"unsupported construct {op}")


def translate(pattern: str) -> str:
    parsed = P.parse(pattern, 0)
    flags = parsed.state.flags
    if flags & (C.SRE_FLAG_ASCII | C.SRE_FLAG_LOCALE):
        raise Refused("ASCII and LOCALE flags are not supported")
    # `regex` builds a first-character prefilter for a pattern; when the pattern starts with an
    # optional case-insensitive item, that prefilter folds the FOLLOWING case-sensitive set too
    # and skips positions `re` matches (measured: `(?i:a)?[^\p{L}]` misses U+0345). A leading
    # always-true lookahead with an alternation disables that prefilter.
    out: list[str] = []
    _emit(parsed, flags, out)
    text = "".join(out)
    return r"(?=[\s\S]|\Z)" + text if "(?i:" in text else text

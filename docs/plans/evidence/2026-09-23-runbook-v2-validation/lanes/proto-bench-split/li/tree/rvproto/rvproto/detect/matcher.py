"""ONE multi-pattern pass over a text (Hyperscan; Python `re` fallback for parity).

Returns maximal, validated (detector, start, end) char spans. Hyperscan reports
every match end with the leftmost start (SOM_LEFTMOST); overlapping reports of
the same detector are merged into maximal spans, then validated (Luhn, entropy).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from rvproto.detect.patterns import PATTERNS, PatternSpec

try:  # pragma: no cover - import guard
    import hyperscan as _hs
except ImportError:  # pragma: no cover
    _hs = None

Hit = tuple[str, int, int]
_VALUE_RUN = re.compile(r"[A-Za-z0-9_/+=.-]+$")
_ENTROPY_MIN_BITS = 3.0


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _entropy(s: str) -> float:
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _card_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    groups = [(m.start() + start, m.end() + start) for m in re.finditer(r"[0-9]+", text[start:end])]
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(groups):
        found = False
        for j in range(len(groups), i, -1):
            digits = "".join(text[a:b] for a, b in groups[i:j])
            if 13 <= len(digits) <= 19 and _luhn(digits):
                out.append((groups[i][0], groups[j - 1][1]))
                i = j
                found = True
                break
        if not found:
            i += 1
    return out


def _validate(spec: PatternSpec, text: str, start: int, end: int) -> list[tuple[int, int]]:
    if spec.validator == "luhn":
        return _card_spans(text, start, end)
    if spec.validator == "entropy":
        m = _VALUE_RUN.search(text[start:end])
        if m is None or _entropy(m.group(0)) < _ENTROPY_MIN_BITS:
            return []
    return [(start, end)]


def _merge(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a < out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


class Matcher:
    def __init__(self, engine: str = "auto") -> None:
        self.specs = PATTERNS
        use_hs = engine == "hyperscan" or (engine == "auto" and _hs is not None)
        self.engine = "hyperscan" if use_hs else "re"
        if use_hs:
            assert _hs is not None, "hyperscan requested but not importable"
            self._db = _hs.Database(mode=_hs.HS_MODE_BLOCK)
            flags = [
                _hs.HS_FLAG_SOM_LEFTMOST | (_hs.HS_FLAG_CASELESS if p.caseless else 0)
                for p in PATTERNS
            ]
            self._db.compile(
                expressions=[p.regex.encode() for p in PATTERNS],
                ids=list(range(len(PATTERNS))),
                elements=len(PATTERNS),
                flags=flags,
            )
            self._scratch = _hs.Scratch(self._db)
        else:
            parts = []
            for i, p in enumerate(PATTERNS):
                body = f"(?i:{p.regex})" if p.caseless else p.regex
                parts.append(f"(?P<g{i}>{body})")
            self._re = re.compile("|".join(parts))

    def scan(self, text: str, only: frozenset[str] | None = None) -> list[Hit]:
        raw = self._raw_hs(text) if self.engine == "hyperscan" else self._raw_re(text)
        hits: list[Hit] = []
        for idx, spans in raw.items():
            spec = self.specs[idx]
            if only is not None and spec.detector not in only:
                continue
            for a, b in _merge(spans):
                for s, e in _validate(spec, text, a, b):
                    hits.append((spec.detector, s, e))
        hits.sort(key=lambda h: (h[1], h[2]))
        return hits

    def _raw_hs(self, text: str) -> dict[int, list[tuple[int, int]]]:
        data = text.encode("utf-8")
        raw: dict[int, list[tuple[int, int]]] = {}

        def on_match(idx: int, start: int, end: int, flags: int, ctx: object) -> int:
            lst = raw.get(idx)
            if lst is None:
                raw[idx] = [(start, end)]
            else:
                lst.append((start, end))
            return 0

        self._db.scan(data, match_event_handler=on_match, scratch=self._scratch)
        if raw and len(data) != len(text):
            return {k: [(_char(data, a), _char(data, b)) for a, b in v] for k, v in raw.items()}
        return raw

    def _raw_re(self, text: str) -> dict[int, list[tuple[int, int]]]:
        raw: dict[int, list[tuple[int, int]]] = {}
        for m in self._re.finditer(text):
            idx = int(m.lastgroup[1:])  # type: ignore[index]
            raw.setdefault(idx, []).append(m.span())
        return raw


def _char(data: bytes, off: int) -> int:
    return len(data[:off].decode("utf-8", errors="ignore"))

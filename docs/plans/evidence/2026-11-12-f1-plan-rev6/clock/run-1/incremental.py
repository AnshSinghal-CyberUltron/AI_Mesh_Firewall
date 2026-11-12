"""Incremental streaming scan of one customer rule, exactly equal to a whole-text rescan.

Today the streaming egress rescans the WHOLE accumulated output on every delta
(`egress/stream.py:548`), so a delta costs O(output length) and a stream O(n^2).

For a rule whose read reach R is finite (`translate_prototype.analyze`) and which cannot match
the empty string, a resume point makes each delta cost O(delta + R) while the hit list stays
identical to `finditer(whole_text)`:

* An attempt at position t reads only text[t-lookbehind : t+R]. Once t + R <= len(text), its
  outcome (and, for a match, its span) can never change as the text grows.
* So after a scan, every match with start + R <= len is FINAL, and every failed attempt at
  t < min(next non-final match start, len - R + 1) is a final failure.
* The next scan resumes at q = max(end of last final match, min(next non-final start, len-R+1))
  with `finditer(text, pos=q)`: `pos` keeps the text before q visible to lookbehinds, and `^`/`\\A`
  do not match at pos > 0, exactly as in a whole-text scan.

A rule with unbounded reach (`*`, `+`, `{n,}`, backreferences) or that can match empty keeps
the whole-text rescan (`incremental = False`); the plan bounds its cost differently (§5.2).
"""
from __future__ import annotations

import regex

from translate_prototype import INF, check_limits, stopper, translate


class RuleScanner:
    def __init__(self, pattern: str) -> None:
        a = check_limits(pattern)
        self.rx = regex.compile(translate(pattern))
        self.reach = a.reach
        stop = stopper(pattern) if a.min_width > 0 else None
        self.stop = regex.compile(stop) if stop else None
        self.incremental = a.min_width > 0 and (a.reach != INF or self.stop is not None)
        self.q = 0
        self.z = -1              # last stopper position seen
        self.stop_upto = 0       # stoppers searched up to here
        self.final: list[tuple[int, int]] = []
        self.chars_scanned = 0

    def _last_stopper(self, text: str) -> None:
        if self.stop is None:
            return
        for m in self.stop.finditer(text, pos=max(self.stop_upto, self.q)):
            self.z = m.start()
        self.stop_upto = len(text)

    def scan(self, text: str, timeout: float = 1.0) -> list[tuple[int, int]]:
        n = len(text)
        if not self.incremental:
            self.chars_scanned += n
            return [m.span() for m in self.rx.finditer(text, timeout=timeout, concurrent=True)]
        self.chars_scanned += n - self.q
        found = [m.span() for m in self.rx.finditer(text, pos=self.q, timeout=timeout, concurrent=True)]
        self._last_stopper(text)
        # An attempt at t is final once t + R <= n (reach) or a stopper sits at or after t.
        horizon = max(n - self.reach if self.reach != INF else -1, self.z)
        k = 0
        while k < len(found) and found[k][0] <= horizon:
            k += 1
        new_final, pending = found[:k], found[k:]
        self.final.extend(new_final)
        last_end = new_final[-1][1] if new_final else self.q
        next_start = pending[0][0] if pending else n + 1
        self.q = int(max(last_end, min(next_start, horizon + 1), self.q))
        return self.final + pending

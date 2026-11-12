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
the whole-text rescan (`incremental = False`) unless a stopper makes it final (see `stopper`).

Timeouts (revision 6, Reviewer A, revision 5, S1). A pattern's cost need not grow with the text:
`^(\\w+\\s?){1,30}[.!?]` backtracks exponentially on six words with no terminator yet, and matches
the whole sentence in 0.06 ms once the '.' arrives. Two policies:

* `scan()` (policy "refuse", revision 5): a timeout propagates and the stream is refused. The
  guarantee is then "refused when a pass over the text received SO FAR exceeds its budget", which
  can refuse a stream whose complete text scans quickly (a documented limitation).
* `scan_or_hold()` (policy "hold"): a timeout on a pass that is not the final one leaves the rule
  UNDECIDED. Nothing at or after the resume point q is released (every attempt before q is final
  and decided), and the pass is retried only when a stopper arrives after q, when the text has
  grown by `growth` (2x) since the failed pass, or at the end of the stream. Only the final pass
  (or the existing hold cap) refuses. The final pass does a subset of the whole-text scan's work
  under the whole text's budget, so a stream is refused only where the same text, scanned once
  non-streaming, would be, or where the rule stays undecided over `hold_cap` characters (4 KiB).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import regex

from translate_prototype import INF, check_limits, stopper, translate


@dataclass(frozen=True)
class Undecided:
    """The rule timed out on a non-final pass: hold everything from `hold_from` on."""
    hold_from: int


class RuleScanner:
    def __init__(self, pattern: str, growth: float = 2.0, stopper_retry: str = "always",
                 hold_cap: int = 4096) -> None:
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
        # policy "hold" state
        self.growth = growth
        self.stopper_retry = stopper_retry   # "always" (A's rule) or "once" (once per growth step)
        self.hold_cap = hold_cap             # undecided text held beyond this: refuse
        self.failed_at: int | None = None    # text length of the last timed-out pass
        self.stop_retry_used = False
        self.timeouts = 0
        self.timeout_cpu = 0.0               # thread CPU spent in timed-out passes

    def _last_stopper(self, text: str) -> None:
        if self.stop is None:
            return
        for m in self.stop.finditer(text, pos=max(self.stop_upto, self.q)):
            self.z = m.start()
        self.stop_upto = len(text)

    def scan(self, text: str, timeout: float = 1.0) -> list[tuple[int, int]]:
        """Policy "refuse": the hit list of `text`, or TimeoutError. State is unchanged on a timeout."""
        n = len(text)
        if timeout <= 0:
            raise TimeoutError("no budget left")  # `regex` reads a negative timeout as none
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

    def _stopper_since(self, text: str, start: int) -> bool:
        return self.stop is not None and self.stop.search(text, pos=max(start, self.q)) is not None

    def scan_or_hold(self, text: str, timeout: float = 1.0, final: bool = False):
        """Policy "hold": the hit list, or `Undecided(hold_from)`; TimeoutError only on the final pass."""
        n = len(text)
        if self.failed_at is not None and n - (self.q if self.incremental else 0) > self.hold_cap:
            raise TimeoutError("undecided over the hold cap")
        if self.failed_at is not None and not final:
            grown = n >= self.growth * max(self.failed_at, 1)
            stop = False
            if not grown and (self.stopper_retry == "always" or not self.stop_retry_used):
                stop = self._stopper_since(text, self.failed_at)
            if not grown and not stop:
                return Undecided(self.q if self.incremental else 0)
            if stop and not grown:
                self.stop_retry_used = True
        c0 = time.thread_time()
        try:
            hits = self.scan(text, timeout)
        except TimeoutError:
            self.timeouts += 1
            self.timeout_cpu += time.thread_time() - c0
            if final:
                raise
            if self.failed_at is None or n >= self.growth * max(self.failed_at, 1):
                self.stop_retry_used = False  # a new growth step: one stopper retry again
            self.failed_at = n
            return Undecided(self.q if self.incremental else 0)
        self.failed_at = None
        self.stop_retry_used = False
        return hits

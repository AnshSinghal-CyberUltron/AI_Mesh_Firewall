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

Revision 7 (Plan Reviewer A, revision 6, S1; Reviewer B NIT 13; issue #158):
* stopper-gated after a timeout (`gate=True`): once a rule has timed out in a stream, its later
  non-final passes run only when a new stopper has arrived since its last pass (or the text has
  doubled since then, or at the end), even after it is decided again. Revision 6 went back to a pass
  per delta after each success, so every sentence or bullet opened a new undecided episode that
  cost one timed-out pass (A: 92 timed-out passes, 13.7 s, on 6 KiB of quoted sentences);
* the cost bound is per STREAM: after `max_timeouts` (4) timed-out non-final passes in a stream,
  the rule makes no more non-final passes; it holds until the final pass decides it or the hold cap
  refuses. So the CPU of timed-out passes is at most max_timeouts x the per-pass budget per rule
  per stream, plus the final pass;
* the hold cap measures the text HELD: the characters since the rule's last decided pass (the
  stream released everything before that point and cannot recall it), not n - q (q can stay at 0 on
  stopper-poor text, and a whole-text-rescan rule always held from 0). The cap is 4,096 characters
  (code points), up to 16 KiB of UTF-8;
* a MemoryError (the per-pass match-memory cap) refuses the pass at once, on any pass;
* `keep_final=False` (the gateway's mode) returns only the hits not yet returned, so a delta no
  longer copies the stream's whole final hit list (B NIT 13: quadratic in hits per stream);
* `scan_pass` gives a pass's shared budget to the rules in a fixed order: rules that have not timed
  out in this stream first, cheapest recent pass first (an EWMA of each rule's pass CPU), then the
  rules that have; when the budget runs out, every rule not yet scanned in that pass is undecided at
  its own resume point (that is not a timeout of that rule and does not count).
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
                 hold_cap: int = 4096, max_timeouts: int | None = None, keep_final: bool = True,
                 hold_measure: str = "n-q", gate: bool = False) -> None:
        a = check_limits(pattern)
        self.rx = regex.compile(translate(pattern))
        self.reach = a.reach
        stop = stopper(pattern) if a.min_width > 0 else None
        self.stop = regex.compile(stop) if stop else None
        # Revision 7 (B should-fix 5): the LAST stopper in one C-level search (greedy `.*` then the
        # stopper), instead of a Python match object per stopper character since the last scan.
        self.stop_last = regex.compile("(?s:.*)(" + stop + ")") if stop else None
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
        # revision 7
        self.max_timeouts = max_timeouts     # None: revision 6 (no per-stream bound)
        self.keep_final = keep_final         # False: return only hits not returned before
        self.hold_measure = hold_measure     # "n-q" (revision 6) or "held" (revision 7)
        self.decided_at = 0                  # text length at the last decided pass
        self.gate = gate                     # stopper-gated after the first timeout
        self.gated = False
        self.last_pass_at = 0                # text length at the last pass that ran
        self.cost_ewma = 0.0                 # recent thread CPU per pass (scan_pass ordering)

    def _last_stopper(self, text: str) -> None:
        if self.stop is None:
            return
        m = self.stop_last.search(text, pos=max(self.stop_upto, self.q))
        if m is not None:
            self.z = m.start(1)
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
        if self.keep_final:
            self.final.extend(new_final)
        last_end = new_final[-1][1] if new_final else self.q
        next_start = pending[0][0] if pending else n + 1
        self.q = int(max(last_end, min(next_start, horizon + 1), self.q))
        return self.final + pending if self.keep_final else new_final + pending

    def _stopper_since(self, text: str, start: int) -> bool:
        return self.stop is not None and self.stop.search(text, pos=max(start, self.q)) is not None

    def _hold_from(self) -> int:
        if self.hold_measure == "held":
            return max(self.q if self.incremental else 0, self.decided_at)
        return self.q if self.incremental else 0

    def scan_or_hold(self, text: str, timeout: float = 1.0, final: bool = False):
        """Policy "hold": the hit list, or `Undecided(hold_from)`; TimeoutError only on the final pass
        (and at the hold cap); MemoryError on any pass (revision 7: a refused pass)."""
        n = len(text)
        if self.failed_at is not None and n - self._hold_from() > self.hold_cap:
            raise TimeoutError("undecided over the hold cap")
        if (self.failed_at is not None and not final and self.max_timeouts is not None
                and self.timeouts >= self.max_timeouts):
            return Undecided(self._hold_from())  # revision 7: no more non-final retries in this stream
        if self.gated and self.failed_at is None and not final:
            # revision 7: decided again, but this rule has timed out in this stream: pass only at a
            # new stopper or on doubling, so a new sentence does not open a new timed-out episode
            if (n < self.growth * max(self.last_pass_at, 1)
                    and not self._stopper_since(text, self.last_pass_at)):
                return Undecided(self._hold_from())
        if self.failed_at is not None and not final:
            grown = n >= self.growth * max(self.failed_at, 1)
            stop = False
            if not grown and (self.stopper_retry == "always" or not self.stop_retry_used):
                stop = self._stopper_since(text, self.failed_at)
            if not grown and not stop:
                return Undecided(self._hold_from())
            if stop and not grown:
                self.stop_retry_used = True
        c0 = time.thread_time()
        self.last_pass_at = n
        try:
            hits = self.scan(text, timeout)
        except TimeoutError:
            self.cost_ewma += (time.thread_time() - c0 - self.cost_ewma) / 4
            self.gated = self.gate
            self.timeouts += 1
            self.timeout_cpu += time.thread_time() - c0
            if final:
                raise
            if self.failed_at is None or n >= self.growth * max(self.failed_at, 1):
                self.stop_retry_used = False  # a new growth step: one stopper retry again
            self.failed_at = n
            return Undecided(self._hold_from())
        self.cost_ewma += (time.thread_time() - c0 - self.cost_ewma) / 4
        self.failed_at = None
        self.stop_retry_used = False
        self.decided_at = n
        return hits


def scan_pass(scanners: list[RuleScanner], text: str, budget: float, final: bool = False):
    """One pass over a plan's rules with ONE shared thread-CPU budget (revision 7, A S1).

    Order: rules that have not timed out in this stream, cheapest recent pass first, then those
    that have, so a rule that times out cannot starve the others. Each rule gets what is left of
    the budget. When the budget runs out, every rule not yet scanned in this pass is undecided at its
    own resume point; that is not a timeout of that rule (it does not count toward `max_timeouts`).
    Returns {index: hits | Undecided}; raises TimeoutError (the final pass, or the hold cap) or
    MemoryError (any pass), which refuse the request."""
    order = sorted(range(len(scanners)), key=lambda i: (scanners[i].timeouts > 0, scanners[i].cost_ewma, i))
    deadline = time.thread_time() + budget
    out = {}
    for i in order:
        sc = scanners[i]
        left = deadline - time.thread_time()
        if left <= 0:
            if final:
                raise TimeoutError("the pass's shared budget ran out on the final pass")
            out[i] = Undecided(sc._hold_from())
            continue
        out[i] = sc.scan_or_hold(text, left, final)
    return out

"""Pattern-aware minimal holdback for streaming output.

hold_start(buf) returns the earliest index i such that buf[i:] could still grow
into (or extend) a match of some output pattern. Everything before i cannot be
part of a still-incomplete match and may be released once scanned. Per family:

  word-class run  : email, AWS, GitHub, Slack, Google, Stripe, JWT, IPv4, SSN and
                    the value of a generic secret are made only of these chars,
                    so a trailing run of them may still grow into a match.
  numeric run     : phone/card allow spaces, dots, dashes, parens between digits;
                    hold from the first digit/'('/'+' of a trailing run, bounded
                    by the longest possible numeric match.
  PEM header      : longest suffix that is a proper prefix of a PEM header.
  generic secret  : a keyword followed only by separators/quote at the end.
"""

from __future__ import annotations

import re

from rvproto.detect.patterns import PEM_HEADERS

_WORD = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+@/=-")
_NUM = frozenset("0123456789 ().+-")
_NUM_START = frozenset("0123456789(+")
_CARD_MAX_CHARS = 19 + 18  # 19 digits + one separator between each
_PEM_MAX = max(len(h) for h in PEM_HEADERS)
_GENERIC_TAIL = re.compile(
    r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret"
    r"|password|passwd)[\"']?[ \t]{0,4}(?:[:=][ \t]{0,4}[\"']?[A-Za-z0-9_/+=.-]*)?$"
)
_GENERIC_TAIL_WINDOW = 64


def _word_run(buf: str, n: int) -> int:
    i = n
    while i > 0 and buf[i - 1] in _WORD:
        i -= 1
    if i == n:
        return n
    run = buf[i:n]
    ats = [k for k, ch in enumerate(run) if ch == "@"]
    if len(ats) >= 2:  # only the part after the second-to-last '@' can still match
        i += ats[-2] + 1
    return i


def _numeric_run(buf: str, n: int) -> int:
    j = n
    lo = max(0, n - _CARD_MAX_CHARS)
    while j > lo and buf[j - 1] in _NUM:
        j -= 1
    for k in range(j, n):
        if buf[k] in _NUM_START:
            return k
    return n


def _pem_prefix(buf: str, n: int) -> int:
    lo = max(0, n - _PEM_MAX)
    k = buf.find("-", lo)
    while k != -1 and k < n:
        tail = buf[k:n]
        for h in PEM_HEADERS:
            if len(tail) < len(h) and h.startswith(tail):
                return k
        k = buf.find("-", k + 1)
    return n


def hold_start(buf: str) -> int:
    n = len(buf)
    if n == 0:
        return 0
    w = _word_run(buf, n)
    h = min(w, _numeric_run(buf, n))
    if "-" in buf[max(0, n - _PEM_MAX) :]:
        h = min(h, _pem_prefix(buf, n))
    # a keyword can only precede the trailing value run by a few separator chars
    lo = max(0, w - _GENERIC_TAIL_WINDOW)
    m = _GENERIC_TAIL.search(buf, lo)
    if m is not None:
        h = min(h, m.start())
    return h

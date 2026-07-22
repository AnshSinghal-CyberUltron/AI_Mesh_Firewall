"""G79: the render-invisible-emphasis token regex must stay LINEAR (possessive value runs).

A long value-char model OUTPUT with no separator (the output path does NOT apply the input's 10k
length cap) previously backtracked the `{1,256}` value quantifier at every start position -> O(256*n)
(~1.5s for 200KB, a soft output-side DoS). Making the value runs possessive ({1,256}+) makes each
start O(1); this is semantically identical because the separator always starts with a char disjoint
from the value class ([*`<] vs [\\w@.\\-]), so backtracking a value run can never help find a match.

These freeze (a) the linear timing (catches a regression back to the non-possessive form) and (b) that
a value split by a render-invisible separator is still tokenized (so the guard can strip it and re-detect).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import output_guard as og  # noqa: E402


def test_emph_html_regex_is_linear_on_pathological_value_run():
    bad = "a@.-" * 50000  # 200KB of value-class chars, no separator -> value-quantifier worst case
    start = time.perf_counter()
    og._EMPH_HTML_TOKEN_RE.findall(bad)
    elapsed = time.perf_counter() - start
    # Fixed (possessive) ~0.14s; the O(256*n) regression was ~1.5s. Generous bound (7x margin) catches it.
    assert elapsed < 1.0, (
        f"_EMPH_HTML_TOKEN_RE took {elapsed:.2f}s on 200KB -> DoS regression (value runs no longer possessive?)"
    )


def test_emph_html_regex_still_matches_valid_render_invisible_split():
    # correctness: a value split by a render-invisible separator must still tokenize, so the guard
    # strips the separator and re-detects the hidden PII/secret.
    assert og._EMPH_HTML_TOKEN_RE.findall("john.doe<!-- x -->@example.com")
    assert og._EMPH_HTML_TOKEN_RE.findall("sec*ret*key")

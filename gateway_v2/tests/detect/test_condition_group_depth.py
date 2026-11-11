"""Hotfix: a customer regex can no longer raise out of the condition compiler or scanner.

`re` parses and compiles recursively, so a 999-char pattern of 499 nested groups raises
`RecursionError` (not `re.error`), `a{4294967296}` raises `OverflowError`, and a ~400-deep
pattern compiles or raises depending on how deep the caller's stack already is. Each is now a
refused rule, decided by `group_depth` BEFORE `re` sees the pattern, so the answer is the same
on every stack.

The `GROUP_DEPTH_VECTORS` below are pinned verbatim in the control twin's suite
(`control/ai_mesh_control/policy/tests/test_regex_group_depth.py`); change them together.
"""

from __future__ import annotations

import random
import re
import threading
import warnings
from typing import Any

import pytest

from gateway_v2.detect import conditions
from gateway_v2.detect.conditions import (
    MAX_GROUP_DEPTH,
    compile_conditions,
    group_depth,
    scan_conditions,
)
from gateway_v2.domain import (
    Action,
    Category,
    FailurePosture,
    Mode,
    Phase,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    Surface,
)
from gateway_v2.domain.posture import POLICY_REGEX_TIMEOUT


def _nested(depth: int, core: str = "a") -> str:
    return "(" * depth + core + ")" * depth


# (pattern, depth group_depth must report). Pinned verbatim in the control twin's suite.
GROUP_DEPTH_VECTORS: tuple[tuple[str, int], ...] = (
    ("", 0),
    ("abc", 0),
    ("(a)", 1),
    ("(a)(b)(c)", 1),
    ("((a))", 2),
    ("(?:(?P<n>a)(?=b))", 2),
    (r"\(\(\(a", 0),  # escaped parentheses are literals
    (r"\\(a)", 1),  # an escaped backslash, then a real group
    ("[(]", 0),  # a parenthesis inside a class is a literal
    ("[()]((a))", 2),
    ("[]((((]", 0),  # `]` first in a class is a literal, so the class runs to the last `]`
    ("[^](((]", 0),  # ... and so is `]` right after `[^`
    ("[[(]", 0),  # `[` inside a class is a literal
    (r"[\]((]", 0),  # an escaped `]` does not close the class
    ("[a-]((a))", 2),
    ("(?#(((((()(a)", 1),  # a comment group ends at its first `)`
    (r"(?#\)((((()(a)", 1),  # ... an escaped one does not end it
    ("(?x)#[\n((a))", 2),  # VERBOSE: a `[` inside a `#` comment opens no class
    ("(?x)# (((\na", 0),  # VERBOSE: a `#` comment is skipped, as re does
    ("(?x)[#]((a))", 2),  # `#` inside a class is not a comment
    ("#[((((]", 0),  # not VERBOSE: `#` is a literal and the class is real
    ("(?i)a(b)", 1),  # global flags open no group
    ("(?x:#)\n" + _nested(40) + ")", 41),  # a scoped VERBOSE comment hides its `)`
    # VERBOSE in one scope only: outside it `#[` is a real class that runs past the newline and
    # hides 20 `)` (a fuzz finding against an earlier "maybe VERBOSE" approximation).
    ("(?x:a)" + "(" * 20 + "#[\n" + ")" * 20 + "]" + "(" * 20 + "a" + ")" * 40, 40),
    ("(?x)(?-x:#[\n" + ")" * 20 + "]" + "(" * 20 + "a" + ")" * 21, 21),  # VERBOSE off in a scope
    ("(?P<n>a)(?P=n)", 1),
    ("(a)?(?(1)b|c)", 2),  # over-count: the condition `(1)` counts one level while open
    (
        "(?# \n)#a\n\\[[ )^^a](?#x- )[] ?[:a][-?(a#a](?=#[\\\\\n)*[^a]\\) \\\\(?!\\#"
        "(?x:(?#^]]:))*#)x\n)",
        3,
    ),
    (_nested(32), 32),
    (_nested(33), 33),
    ("(?#[)" + _nested(40), 40),  # a `[` in a comment group cannot hide what follows
    ("(?x)#[\n" + _nested(40), 40),  # nor can a `[` in a VERBOSE comment
    ("(a)(b)" * 40, 1),
    ("[(]" * 40 + "a", 0),
    (r"\(" * 40 + "a" + r"\)" * 40, 0),
)


@pytest.mark.parametrize(("pattern", "expected"), GROUP_DEPTH_VECTORS)
def test_group_depth_vectors(pattern: str, expected: int) -> None:
    assert group_depth(pattern) == expected
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)  # "[[(]": possible nested set
        re.compile(pattern)  # every vector is a pattern `re` accepts at a shallow stack


def test_the_limit_is_thirty_two() -> None:
    assert MAX_GROUP_DEPTH == 32


def _rule(pattern: str, *, rule_id: str = "rx") -> Rule:
    return Rule(
        rule_id=rule_id,
        category=Category.EXFIL,
        mode=Mode.ENFORCE,
        action=Action.BLOCK,
        threshold=None,
        priority=1,
        scope=RuleScope.INPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
        condition=RuleCondition(
            kind=RuleKind.REGEX,
            field="",
            direction=RuleScope.INPUT,
            keywords=(),
            pattern=pattern,
            detector_class="",
            pipeline_stage="",
        ),
    )


def _refused(pattern: str) -> bool:
    (item,) = compile_conditions((_rule(pattern),)).prepared
    assert item.refused == (item.regex is None)
    return item.refused


def test_thirty_two_deep_is_accepted_and_matches() -> None:
    pattern = _nested(32)
    assert not _refused(pattern)
    scanned = scan_conditions(
        compile_conditions((_rule(pattern),)), "xax", budget_s=1.0, phase=Phase.INPUT,
    )
    assert scanned.refuse_code is None and len(scanned.findings) == 1


@pytest.mark.parametrize("depth", [33, 400, 499])
def test_deeper_than_thirty_two_is_refused_without_raising(depth: int) -> None:
    pattern = _nested(depth)
    assert len(_nested(499)) == 999, "the reported pattern fits the 1000-char limit"
    assert not conditions._redos_shape(pattern), "it passes every ReDoS-shape check"
    re.purge()
    assert _refused(pattern)
    scanned = scan_conditions(
        compile_conditions((_rule(pattern),)), "a", budget_s=1.0, phase=Phase.INPUT,
    )
    assert scanned.refuse_code == POLICY_REGEX_TIMEOUT and scanned.findings == ()


def _at_depth(frames: int, pattern: str) -> bool:
    if frames:
        return _at_depth(frames - 1, pattern)
    return _refused(pattern)


@pytest.mark.parametrize("frames", [0, 300, 600])
def test_the_answer_does_not_depend_on_the_callers_stack(frames: int) -> None:
    """Before the fix a 400-deep pattern compiled at a shallow stack and raised after ~200."""
    re.purge()
    assert _at_depth(frames, _nested(400)), "refused at every stack depth"
    re.purge()
    assert not _at_depth(frames, _nested(32)), "accepted at every stack depth"


def test_an_overflowing_repeat_is_refused_not_raised() -> None:
    """`re` raises OverflowError, not re.error, for a repeat count beyond its C range."""
    with pytest.raises(OverflowError):
        re.compile("a{4294967296}")
    assert _refused("a{4294967296}")


def test_re_error_is_still_refused() -> None:
    assert _refused("a(b")


class _Match:
    def __init__(self, start: int, end: int) -> None:
        self._span = (start, end)

    def span(self) -> tuple[int, int]:
        return self._span


class _FaultyPattern:
    """A pattern whose `finditer` yields one match, then raises."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def finditer(self, text: str) -> Any:
        yield _Match(0, 1)
        raise self._exc


class _NotAnException(BaseException):
    pass


@pytest.mark.parametrize(
    "exc", [RuntimeError("engine fault"), MemoryError(), RecursionError(), re.error("x")],
)
def test_a_scan_that_raises_anything_is_a_failed_scan_not_partial_spans(
    exc: BaseException,
) -> None:
    """Before the fix only re.error failed the scan; anything else returned the partial list."""
    pattern: Any = _FaultyPattern(exc)
    assert conditions._regex_spans(pattern, "aaa", 1.0) is None


def test_a_worker_killed_by_a_base_exception_is_a_failed_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threading, "excepthook", lambda _args: None)
    pattern: Any = _FaultyPattern(_NotAnException())
    assert conditions._regex_spans(pattern, "aaa", 1.0) is None


def test_a_completed_scan_still_returns_every_span() -> None:
    spans = conditions._regex_spans(re.compile("a"), "xaxa", 1.0)
    assert spans is not None and [(s.start, s.end) for s in spans] == [(1, 2), (3, 4)]


# --- the property: group_depth never under-counts what `re` actually nests ------------------
#
# `re`'s own parse tree is the ground truth. It is reached through the private `re._parser`
# (3.11+) only here, in a test, and walked iteratively. A scan that under-counted could let a
# deep pattern past the limit; an over-count only refuses more.

def _true_depth(tree: Any) -> int:
    from re import _constants as sre
    from re import _parser as parser

    best, stack = 0, [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        best = max(best, depth)
        for op, av in node.data if isinstance(node, parser.SubPattern) else node:
            if op is sre.SUBPATTERN:
                stack.append((av[3], depth + 1))
            elif op in (sre.ASSERT, sre.ASSERT_NOT):
                stack.append((av[1], depth + 1))
            elif op is sre.ATOMIC_GROUP:
                stack.append((av, depth + 1))
            elif op is sre.GROUPREF_EXISTS:
                stack.extend((branch, depth + 1) for branch in av[1:] if branch is not None)
            elif op is sre.BRANCH:
                stack.extend((branch, depth) for branch in av[1])
            elif op in (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT):
                stack.append((av[2], depth))
    return best


_TOKENS = (
    "(", ")", "(?:", "(?#", "(?x)", "(?x:", "(?-x:", "(?i)", "[", "]", "^", "\\", "\\(", "\\)",
    "\\[", "\\]", "#", "\n", "a", "|", "*", "(?=", "(?<!", "(?>", "(?P<n>", "(?(1)", " ", "[^",
    "-", "{2}", "+",
)
_JUNK = "()[]^\\#\n a|-x?:"
_OPENERS = ("(", "(?:", "(?=", "(?!", "(?<!", "(?>", "(?i:", "(?x:", "(?-x:")


def _junk(rng: random.Random, size: int, forbid: str) -> str:
    out = []
    for _ in range(size):
        char = rng.choice(_JUNK)
        if char == "\\":
            char = "\\" + rng.choice("()[]#an\\")
        if char not in forbid:
            out.append(char)
    return "".join(out)


def _structured(rng: random.Random) -> str:
    """Mostly-valid patterns mixing every construct the scan treats specially."""
    parts = ["(?x)"] if rng.random() < 0.3 else []
    depth = 0
    for _ in range(rng.randint(1, 30)):
        roll = rng.random()
        if roll < 0.25:
            parts.append(rng.choice(_OPENERS))
            depth += 1
        elif roll < 0.45 and depth:
            parts.append(")" + rng.choice(("", "*", "+", "?", "{2}")))
            depth -= 1
        elif roll < 0.6:
            body = _junk(rng, rng.randint(0, 6), "]\\")
            parts.append(f"[{rng.choice(('', '^'))}{rng.choice(('', ']'))}{body}a]")
        elif roll < 0.7:
            parts.append("(?#" + _junk(rng, rng.randint(0, 6), ")") + ")")
        elif roll < 0.8:
            parts.append("#" + _junk(rng, rng.randint(0, 6), "\n") + "\n")
        elif roll < 0.9:
            parts.append(rng.choice(("\\(", "\\)", "\\[", "\\]", "\\\\", "\\#")))
        else:
            parts.append(rng.choice(("a", "b|c", " ")))
    return "".join(parts) + ")" * depth


def _random_tokens(rng: random.Random) -> str:
    """Adversarial token soup; most of it does not compile and is skipped."""
    return "".join(rng.choice(_TOKENS) for _ in range(rng.randint(1, 40)))


@pytest.mark.parametrize(
    ("generator", "samples", "minimum"),
    [(_structured, 4_000, 2_000), (_random_tokens, 30_000, 800)],
)
def test_group_depth_never_under_counts_what_re_nests(
    generator: Any, samples: int, minimum: int,
) -> None:
    from re import _parser as parser

    rng = random.Random(0x32_DEE9)
    checked = 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for _ in range(samples):
            pattern = generator(rng)
            try:
                tree = parser.parse(pattern)
            except (re.error, RecursionError, OverflowError):
                continue
            checked += 1
            assert group_depth(pattern) >= _true_depth(tree), repr(pattern)
    assert checked >= minimum, checked

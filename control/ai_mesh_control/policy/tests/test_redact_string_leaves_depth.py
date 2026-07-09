"""#30 regression: scope='entire' string-leaf scrubbing depth (policy.redaction._redact_string_leaves).

Contract under test:
  * A scope='entire' hint scrubs its regex/keywords from EVERY string leaf.
  * The old RECURSIVE depth-10 cap was a REDACTION BYPASS: string leaves nested
    11..500 deep egressed UNREDACTED while the audit trail said "redacted". The
    gateway admits payloads to _MCP_MAX_RESULT_DEPTH=500 and apply_field_redaction
    (CHG-0148) masks to depth 500 — this sibling now matches (iterative, cap 500,
    no RecursionError at any depth).

Assertions validated against the REAL source (apply_redaction + _smart_substitute +
_redact_string_leaves) via AST extraction: scratchpad/test_redact_string_leaves_depth_ast_proof.py.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from policy.redaction import _REDACT_STRING_LEAVES_MAX_DEPTH, _redact_string_leaves

_HINT = [{"config": {"regex": "SECRET", "replacement": "[MASK]"}}]


def _nest(inner: dict, depth: int) -> dict:
    node = inner
    for _ in range(depth):
        node = {"wrap": node}
    return node


def _dig(node: dict, depth: int) -> dict:
    for _ in range(depth):
        node = node["wrap"]
    return node


class RedactStringLeavesDepthTests(SimpleTestCase):
    def test_shallow_leaf_redacted(self):
        out = _redact_string_leaves({"a": "my SECRET"}, _HINT, "[MASK]")
        self.assertEqual(out["a"], "my [MASK]")

    def test_deeply_nested_leaf_now_redacted(self):
        # depth 15 — EGRESSED UNREDACTED under the old depth-10 cap.
        payload = _nest({"leaf": "my SECRET"}, 15)
        out = _redact_string_leaves(payload, _HINT, "[MASK]")
        self.assertEqual(_dig(out, 15)["leaf"], "my [MASK]")

    def test_leaf_at_cap_boundary_redacted(self):
        payload = _nest({"leaf": "my SECRET"}, _REDACT_STRING_LEAVES_MAX_DEPTH - 5)
        out = _redact_string_leaves(payload, _HINT, "[MASK]")
        self.assertEqual(_dig(out, _REDACT_STRING_LEAVES_MAX_DEPTH - 5)["leaf"], "my [MASK]")

    def test_beyond_cap_no_recursion_error(self):
        # 600 deep: bounded (leaf beyond 500 may stay), but crucially NO
        # RecursionError (iterative walk).
        payload = _nest({"leaf": "my SECRET"}, 600)
        try:
            _redact_string_leaves(payload, _HINT, "[MASK]")
        except RecursionError:
            self.fail("RecursionError — walk must be iterative")

    def test_root_string_redacted(self):
        self.assertEqual(_redact_string_leaves("my SECRET", _HINT, "[MASK]"), "my [MASK]")

    def test_list_leaves_redacted(self):
        out = _redact_string_leaves({"items": ["a SECRET", {"n": "b SECRET"}]}, _HINT, "[MASK]")
        self.assertEqual(out["items"][0], "a [MASK]")
        self.assertEqual(out["items"][1]["n"], "b [MASK]")

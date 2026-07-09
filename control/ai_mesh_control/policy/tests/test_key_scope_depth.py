"""#29 regression: scope='key' matcher depth coverage (policy.engine._collect_key_values).

Contract under test:
  * A scope='key' rule targets a field by NAME anywhere in the arg/response tree.
  * The old RECURSIVE depth-10 cap was a DETECTION BYPASS: a key nested 11..500
    deep was silently NOT collected, so the rule's block/redact action never
    fired for deeply-nested occurrences — even though the gateway admits payloads
    up to _MCP_MAX_RESULT_DEPTH=500 and apply_field_redaction (CHG-0148) masks to
    depth 500. The matcher is now ITERATIVE with cap 500 to match those siblings
    (no RecursionError at any depth; a node cap bounds pathological width).

Proven independently against the REAL source via AST extraction (no Django):
scratchpad/test_key_scope_depth_ast_proof.py.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from policy.engine import (
    _KEY_COLLECT_MAX_DEPTH,
    _collect_key_values,
)


def _nest(inner: dict, depth: int, wrapper_key: str = "wrap") -> dict:
    node = inner
    for _ in range(depth):
        node = {wrapper_key: node}
    return node


class KeyScopeDepthTests(SimpleTestCase):
    def test_shallow_key_collected(self):
        out = _collect_key_values({"a": {"secret_field": "SHALLOW"}}, "secret_field")
        self.assertIn("SHALLOW", out)

    def test_deeply_nested_key_now_collected(self):
        # Nested 15 deep — MISSED by the old depth-10 cap; now collected.
        payload = _nest({"secret_field": "DEEP-SECRET-VALUE"}, 15)
        out = _collect_key_values(payload, "secret_field")
        self.assertIn("DEEP-SECRET-VALUE", out)

    def test_key_at_cap_boundary_collected(self):
        # Just inside the 500 cap (the gateway's admitted max depth).
        payload = _nest({"secret_field": "AT-CAP"}, _KEY_COLLECT_MAX_DEPTH - 5)
        out = _collect_key_values(payload, "secret_field")
        self.assertIn("AT-CAP", out)

    def test_beyond_cap_bounded_no_recursion_error(self):
        # 600 deep: beyond the cap -> bounded ([]), and crucially NO
        # RecursionError (the walk is iterative, unlike the old recursive form).
        payload = _nest({"secret_field": "TOO-DEEP"}, 600)
        out = _collect_key_values(payload, "secret_field")
        self.assertEqual(out, [])

    def test_case_insensitive_nfkc_key_match_preserved(self):
        # Semantics preserved: NFKC + case-insensitive key match.
        out = _collect_key_values({"Secret_Field": "MIXEDCASE"}, "secret_field")
        self.assertIn("MIXEDCASE", out)

    def test_matched_key_not_descended_into(self):
        # A matched key's value is collected but NOT re-walked (original
        # recursive semantics: descend only into NON-matching branches).
        payload = {"secret_field": {"secret_field": "INNER"}}
        out = _collect_key_values(payload, "secret_field")
        # Outer match collects the whole dict json-ified; inner is not separately
        # descended into, so exactly one collected value.
        self.assertEqual(len(out), 1)

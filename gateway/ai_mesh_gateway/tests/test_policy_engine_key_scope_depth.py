"""#31 regression: gateway scope='key' matcher depth (policy_engine._collect_key_values).

Gateway twin of control #29. The old RECURSIVE depth-10 cap was a DETECTION BYPASS:
a scope='key' rule targeting a field nested 11..500 deep was silently NOT matched,
so its block/redact action never fired. The gateway admits payloads to
_MCP_MAX_RESULT_DEPTH=500 and apply_field_redaction (CHG-0148) masks to 500 — this
matcher was the inconsistent sibling. Now iterative with cap 500 (no RecursionError).

Assertions validated against the REAL source via AST extraction (no import deps):
proven green in-session (shallow / depth-15 / depth-600 bounded / case-insensitive /
matched-not-descended).
"""

import unittest

from ai_mesh_gateway.policy_engine import _KEY_COLLECT_MAX_DEPTH, _collect_key_values


def _nest(inner, depth):
    node = inner
    for _ in range(depth):
        node = {"wrap": node}
    return node


class KeyScopeDepthTests(unittest.TestCase):
    def test_shallow_key_collected(self):
        self.assertIn("SHALLOW", _collect_key_values({"a": {"secret_field": "SHALLOW"}}, "secret_field"))

    def test_deeply_nested_key_now_collected(self):
        payload = _nest({"secret_field": "DEEP-SECRET-VALUE"}, 15)
        self.assertIn("DEEP-SECRET-VALUE", _collect_key_values(payload, "secret_field"))

    def test_at_cap_boundary_collected(self):
        payload = _nest({"secret_field": "AT-CAP"}, _KEY_COLLECT_MAX_DEPTH - 5)
        self.assertIn("AT-CAP", _collect_key_values(payload, "secret_field"))

    def test_beyond_cap_bounded_no_recursion_error(self):
        payload = _nest({"secret_field": "TOO-DEEP"}, 600)
        self.assertEqual(_collect_key_values(payload, "secret_field"), [])

    def test_case_insensitive_nfkc_match_preserved(self):
        self.assertIn("MIXED", _collect_key_values({"Secret_Field": "MIXED"}, "secret_field"))

    def test_matched_key_not_descended_into(self):
        out = _collect_key_values({"secret_field": {"secret_field": "INNER"}}, "secret_field")
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()

"""Hotfix: a customer regex re cannot compile is a 400, never a 500.

``re`` parses and compiles recursively, so a 999-char pattern of 499 nested groups (inside the
1000-char limit and past every ReDoS-shape check) raised RecursionError, not re.error, out of
``validate_condition`` / ``validate_redaction_config``: the API answered 500. The same row then
reached gateway v2 and stopped the projector round for every tenant. ``a{4294967296}`` did the
same with OverflowError.

TWIN: ``GROUP_DEPTH_VECTORS`` is pinned verbatim in
``gateway_v2/tests/detect/test_condition_group_depth.py``; change them together.
"""

from __future__ import annotations

import re
import warnings

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework import serializers as drf_serializers
from rest_framework.test import APIClient

from policy.serializers import (
    MAX_GROUP_DEPTH,
    group_depth,
    validate_condition,
    validate_redaction_config,
)

User = get_user_model()


def _nested(depth, core="a"):
    return "(" * depth + core + ")" * depth


DEEP = _nested(499)

# (pattern, depth group_depth must report). Pinned verbatim in the gateway_v2 twin's suite.
GROUP_DEPTH_VECTORS = (
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
    (_nested(16), 16),
    (_nested(17), 17),
    ("(?#[)" + _nested(40), 40),  # a `[` in a comment group cannot hide what follows
    ("(?x)#[\n" + _nested(40), 40),  # nor can a `[` in a VERBOSE comment
    ("(a)(b)" * 40, 1),
    ("[(]" * 40 + "a", 0),
    (r"\(" * 40 + "a" + r"\)" * 40, 0),
)


class GroupDepthVectorTests(SimpleTestCase):
    def test_vectors(self):
        for pattern, expected in GROUP_DEPTH_VECTORS:
            with self.subTest(pattern=pattern[:40]):
                self.assertEqual(group_depth(pattern), expected)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)  # "[[(]": possible nested set
                    re.compile(pattern)

    def test_the_limit_is_sixteen(self):
        # 16, not 32: the #117 reviews' measurements; see MAX_GROUP_DEPTH in serializers.py.
        self.assertEqual(MAX_GROUP_DEPTH, 16)


class ValidatorTests(SimpleTestCase):
    """The validators alone: every failure is a ValidationError (a 400), never another error."""

    def test_sixteen_deep_is_accepted(self):
        value = {"regex": _nested(16), "field": "prompt"}
        self.assertEqual(validate_condition(value), value)
        self.assertEqual(validate_redaction_config({"regex": _nested(16)}), {"regex": _nested(16)})

    def test_deeper_is_a_validation_error_in_both_fields(self):
        self.assertEqual(len(DEEP), 999)
        for depth in (17, 32, 33, 400, 499):
            for validator in (validate_condition, validate_redaction_config):
                for key in ("regex", "pattern"):
                    with self.subTest(depth=depth, validator=validator.__name__, key=key):
                        re.purge()
                        with self.assertRaisesRegex(
                            drf_serializers.ValidationError, "deeper than 16 levels",
                        ):
                            validator({key: _nested(depth)})

    def test_any_compile_exception_is_a_validation_error(self):
        with self.assertRaises(OverflowError):
            re.compile("a{4294967296}")  # not re.error
        for validator in (validate_condition, validate_redaction_config):
            with self.subTest(validator=validator.__name__):
                with self.assertRaisesRegex(drf_serializers.ValidationError, "does not compile"):
                    validator({"regex": "a{4294967296}"})
                with self.assertRaisesRegex(drf_serializers.ValidationError, "does not compile"):
                    validator({"regex": "a(b"})


class RuleApiTests(TestCase):
    """End to end through the rule endpoint: 400, not 500, and nothing is stored."""

    def setUp(self):
        from auth.models import Organization, UserProfile
        from policy.models import Policy

        self.org = Organization.objects.create(name="Depth Org", slug="depth-org")
        self.user = User.objects.create_user(
            username="depth_admin", password="pw", is_staff=True, is_superuser=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.policy = Policy.objects.create(
            name="Depth policy", code="DEPTH_POLICY", policy_domain="pipeline",
            organization=self.org, category="pii", severity="HIGH", enabled=True, priority=100,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self, **fields):
        payload = {"name": "depth rule", "rule_type": "regex", "action": "block", **fields}
        return self.client.post(
            f"/api/policies/{self.policy.id}/rules/", payload, format="json",
        )

    def test_a_499_deep_condition_is_a_400(self):
        re.purge()
        response = self._post(condition={"regex": DEEP, "field": "prompt"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("condition", response.json())
        self.assertFalse(self.policy.rules.exists())

    def test_a_499_deep_redaction_config_is_a_400(self):
        re.purge()
        response = self._post(
            action="redact",
            condition={"regex": "secret", "field": "prompt"},
            redaction_config={"regex": DEEP},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("redaction_config", response.json())
        self.assertFalse(self.policy.rules.exists())

    def test_a_17_deep_condition_is_a_400(self):
        response = self._post(condition={"regex": _nested(17, "secret"), "field": "prompt"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("deeper than 16 levels", str(response.json()["condition"]))
        self.assertFalse(self.policy.rules.exists())

    def test_a_16_deep_condition_is_created(self):
        response = self._post(condition={"regex": _nested(16, "secret"), "field": "prompt"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(self.policy.rules.exists())


class RuntimeCompileSiteTests(SimpleTestCase):
    """Runtime compile sites: any exception takes the SAME path re.error already takes.

    Those paths are pre-existing and fail-OPEN for the one rule (it never matches; the text is
    not redacted). This hotfix does not change them; it stops RecursionError/OverflowError from
    escaping as a 500 instead. Reported in the PR.
    """

    def test_engine_skips_the_rule_exactly_as_for_re_error(self):
        from policy.engine import _evaluate_rule
        from policy.models import Rule

        for pattern in (DEEP, "a{4294967296}", "a(b"):
            with self.subTest(pattern=pattern[:20]):
                re.purge()
                rule = Rule(id=1, rule_type="regex", action="block", condition={"regex": pattern})
                with self.assertLogs("policy.engine", level="WARNING"):
                    self.assertFalse(_evaluate_rule(rule, {"prompt": "a a(b"}))

    def test_redaction_returns_the_text_exactly_as_for_re_error(self):
        from policy.redaction import apply_redaction

        for pattern in (DEEP, "a{4294967296}", "a(b"):
            with self.subTest(pattern=pattern[:20]):
                re.purge()
                hints = [{"config": {"regex": pattern}, "condition": {}}]
                self.assertEqual(apply_redaction("keep a(b", hints), "keep a(b")

    def test_publisher_does_not_forward_a_row_template_it_cannot_check(self):
        from core.firewall_v2_publish import _matches_own_pattern

        for pattern in (DEEP, "a{4294967296}", "a(b"):
            with self.subTest(pattern=pattern[:20]):
                re.purge()
                self.assertTrue(_matches_own_pattern({"regex": pattern}, "Removed by policy"))

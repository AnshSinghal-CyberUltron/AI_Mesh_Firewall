"""GW36 S4 (Req 8): property test — control budget range validation.

# Feature: gw36-s4-admission-quota, Property 9: Control budget range validation
# Validates: Requirements 8.1, 8.3

Property 9 (design Correctness Properties): for a per-org token budget the
Control_Plane stores a non-negative integer in ``[0, 1_000_000_000]`` (Req 8.1).
A write that is non-integer, negative, or above the ceiling is REJECTED and the
previously stored value is retained unchanged (Req 8.3).

The field under test is ``FirewallConfig.org_token_budget`` — a
``PositiveIntegerField(default=100_000, validators=[MinValueValidator(0),
MaxValueValidator(1_000_000_000)])`` (task 6.1, migration
``0042_firewallconfig_org_token_budget``). This test exercises the two real
validation entry points the control plane uses:

  * the write surface (API): ``PUT /api/firewall/config/`` through
    ``FirewallConfigSerializer`` (``is_valid`` / ``save``), asserting an
    in-range write returns 200 and is stored, and an out-of-range or
    non-integer write returns 400 with the previously stored value retained
    (matches the control suite idiom in ``test_t01_rewrite_reject.py`` and
    ``test_routing_governance_api.py``: ``reverse("firewall-config")`` PUT +
    ``FirewallConfig.load(org)``);
  * the model validator path: ``FirewallConfig.full_clean()``, asserting the
    same accept/reject boundary at the ORM layer.

House idiom for a property: a seeded ``random.Random`` loop (the control suite
uses Django ``TestCase`` rather than the gateway_v2 >= 10,000 idiom, so this runs
a solid >= 100-iteration loop — Django model construction / API round-trips per
iteration are heavy). The generated sequence intentionally spans the four input
classes the task names: integers < 0, integers within ``[0, 1_000_000_000]``,
integers above the ceiling, plus a few non-integer inputs.
"""

from __future__ import annotations

import random

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from auth.models import Organization, UserProfile
from core.models import FirewallConfig

User = get_user_model()

# Field policy values (task 6.1 / migration 0042). Asserted against the live
# model so the test tracks the real validators rather than a hard-coded copy.
BUDGET_MIN = 0
BUDGET_MAX = 1_000_000_000
BUDGET_DEFAULT = 100_000

# >= 100 iterations (task floor). Each iteration is a full API round-trip and/or
# a model full_clean, so 150 is a solid loop without being wastefully slow.
ITERATIONS = 150
SEED = 0x6D36_5334  # "m6S4" — stable, reproducible counter-examples.


def _gen_value(rng: random.Random):
    """Draw one input spanning the four classes Property 9 names.

    Returns ``(value, in_range)`` where ``in_range`` is True only for an integer
    within ``[0, 1_000_000_000]``. Every other draw (negative integer, integer
    above the ceiling, or a non-integer) is out of range and must be rejected.
    """
    kind = rng.randrange(5)
    if kind == 0:
        # Below 0: negative integer.
        return rng.randint(-5_000_000, -1), False
    if kind == 1:
        # Within [0, 1_000_000_000] — includes both boundaries.
        choice = rng.random()
        if choice < 0.1:
            return BUDGET_MIN, True
        if choice < 0.2:
            return BUDGET_MAX, True
        return rng.randint(BUDGET_MIN, BUDGET_MAX), True
    if kind == 2:
        # Above the ceiling.
        return rng.randint(BUDGET_MAX + 1, BUDGET_MAX + 5_000_000), False
    if kind == 3:
        # Non-integer: a float with a fractional part (never a whole number).
        whole = rng.randint(BUDGET_MIN, BUDGET_MAX)
        frac = rng.uniform(0.01, 0.99)
        return whole + frac, False
    # Non-integer / non-numeric: a string that is not a plain integer literal.
    return rng.choice(["not-a-number", "1e3", "12.5", "", "0x10", "  42 x"]), False


class OrgTokenBudgetFieldPolicyTests(TestCase):
    """The validators under test are exactly the task-6.1 policy values."""

    def test_field_default_and_validator_bounds(self):
        field = FirewallConfig._meta.get_field("org_token_budget")
        self.assertEqual(field.default, BUDGET_DEFAULT)
        bounds = {}
        for validator in field.validators:
            limit = getattr(validator, "limit_value", None)
            if limit is None:
                continue
            name = type(validator).__name__
            if "Min" in name:
                bounds["min"] = limit
            elif "Max" in name:
                bounds["max"] = limit
        self.assertEqual(bounds.get("min"), BUDGET_MIN)
        self.assertEqual(bounds.get("max"), BUDGET_MAX)


class OrgTokenBudgetRangeValidationProperty(TestCase):
    """# Feature: gw36-s4-admission-quota, Property 9: Control budget range validation
    # Validates: Requirements 8.1, 8.3
    """

    def setUp(self):
        self.org = Organization.objects.create(
            name="GW36 S4 Budget Org", slug="gw36-s4-budget-org"
        )
        self.user = User.objects.create_user(
            username="gw36-s4-budget-admin",
            email="gw36-s4-budget@example.invalid",
            password="Password123!",
            is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        # Ensure the singleton row exists and start from the declared default.
        config = FirewallConfig.load(organization=self.org)
        self.assertEqual(config.org_token_budget, BUDGET_DEFAULT)

    def _stored(self) -> int:
        return FirewallConfig.load(organization=self.org).org_token_budget

    def test_property_api_write_path(self):
        """Serializer write path (Req 8.1, 8.3): an in-range integer is accepted
        and stored; an out-of-range or non-integer write returns 400 and the
        PREVIOUSLY stored value is retained unchanged."""
        rng = random.Random(SEED)
        # A known-good value so the first reject has a non-default prior value to
        # retain; proves "retain the previously stored value", not just default.
        last_good = 777_777
        res = self.client.put(
            reverse("firewall-config"),
            {"org_token_budget": last_good},
            format="json",
        )
        self.assertEqual(res.status_code, 200, getattr(res, "data", res.content))
        self.assertEqual(self._stored(), last_good)

        for i in range(ITERATIONS):
            value, in_range = _gen_value(rng)
            prior = self._stored()
            res = self.client.put(
                reverse("firewall-config"),
                {"org_token_budget": value},
                format="json",
            )
            if in_range:
                self.assertEqual(
                    res.status_code,
                    200,
                    f"iter {i} value={value!r} should be accepted: "
                    f"{getattr(res, 'data', res.content)}",
                )
                self.assertEqual(
                    self._stored(),
                    int(value),
                    f"iter {i}: accepted value {value!r} must be stored",
                )
                last_good = int(value)
            else:
                self.assertEqual(
                    res.status_code,
                    400,
                    f"iter {i} value={value!r} must be rejected (out of range / "
                    f"non-integer): {getattr(res, 'data', res.content)}",
                )
                self.assertIn(
                    "org_token_budget",
                    getattr(res, "data", {}),
                    f"iter {i}: 400 must name the offending field",
                )
                # Req 8.3: prior value retained unchanged on reject.
                self.assertEqual(
                    self._stored(),
                    prior,
                    f"iter {i}: rejected write {value!r} must retain prior {prior}",
                )

    def test_property_model_full_clean_path(self):
        """Model validator path (Req 8.1, 8.3): full_clean() accepts an in-range
        integer and raises ValidationError for an out-of-range value; a rejected
        value is never persisted, so the stored value is retained unchanged."""
        rng = random.Random(SEED ^ 0xABCDEF)
        config = FirewallConfig.load(organization=self.org)
        # Seed a known-good stored value.
        config.org_token_budget = 123_456
        config.full_clean()
        config.save(update_fields=["org_token_budget"])
        self.assertEqual(self._stored(), 123_456)

        for i in range(ITERATIONS):
            value, in_range = _gen_value(rng)
            # full_clean validators operate on integers / coercible ints; a string
            # or float is exercised through the API path above. Here drive the
            # integer / float boundary (negative, in-range, above ceiling, float).
            if isinstance(value, str):
                continue
            prior = self._stored()
            fresh = FirewallConfig.load(organization=self.org)
            fresh.org_token_budget = value
            if in_range:
                try:
                    fresh.full_clean()
                except ValidationError as exc:  # pragma: no cover - fail path
                    self.fail(
                        f"iter {i}: in-range value {value!r} unexpectedly rejected: {exc}"
                    )
                fresh.save(update_fields=["org_token_budget"])
                self.assertEqual(self._stored(), int(value))
            else:
                with self.assertRaises(
                    ValidationError,
                    msg=f"iter {i}: out-of-range value {value!r} must raise",
                ):
                    fresh.full_clean()
                # Not saved → prior stored value retained unchanged (Req 8.3).
                self.assertEqual(
                    self._stored(),
                    prior,
                    f"iter {i}: rejected value {value!r} must retain prior {prior}",
                )

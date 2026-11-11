"""gw36-s4-admission-quota task 6.2 — unit tests for the control budget field.

Task 6.1 added ``FirewallConfig.org_token_budget`` — a per-org token budget the
projector publishes as a signed ``StateKind.BUDGET`` record and the gateway's
per-org ``BudgetLease`` spends down (Requirement 8). This module is the
example-based unit half (the property test is task 6.3). It pins the three
behaviours task 6.2 names:

  * Default 100,000 on org creation (Req 8.2) — a newly created / default
    ``FirewallConfig`` carries ``org_token_budget == 100_000``.
  * The value is a plain numeric token count with NO credential semantics
    (Req 8.5) — it is a ``PositiveIntegerField`` holding an ``int`` count,
    serialized as a number by ``FirewallConfigSerializer`` (never masked /
    redacted / a string).
  * The migration applies — ``0042_firewallconfig_org_token_budget`` exists,
    depends on ``0041_v2plandocument``, and adds the ``org_token_budget`` field.

The migration check is a lightweight static inspection of the migration
module's ``operations`` / ``dependencies`` so it runs without a live database
(control migrations are Postgres-native, so ``manage.py test`` on sqlite dies
during ``setup_databases``). The DB-backed default/serializer checks use the
house convention shared by the other FirewallConfig tests
(``django.test.TestCase`` + ``Organization.objects.create`` + ``FirewallConfig.load``).
"""

from importlib import import_module

from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models as django_models
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from auth.models import Organization, UserProfile
from core.firewall_config_serializer import FirewallConfigSerializer
from core.models import FirewallConfig

User = get_user_model()

# Declared control-plane policy values (Req 8.1, 8.2). These live in control
# only — never in gateway_v2 — so they are referenced here as literals, same as
# the field declaration in core/models.py.
_DEFAULT_TOKEN_BUDGET = 100_000
_MIN_TOKEN_BUDGET = 0
_MAX_TOKEN_BUDGET = 1_000_000_000


class OrgTokenBudgetDefaultTests(TestCase):
    """Req 8.2 — a newly created org's FirewallConfig defaults to 100,000."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="BudgetDefaultOrg", slug="budget-default-org"
        )

    def test_new_config_defaults_to_100_000(self):
        """A freshly created per-org config carries the declared default."""
        cfg = FirewallConfig.load(self.org)
        self.assertEqual(cfg.org_token_budget, _DEFAULT_TOKEN_BUDGET)

    def test_default_persists_across_reload(self):
        """The default is stored, not just an in-memory attribute default."""
        FirewallConfig.load(self.org)  # create + persist
        reloaded = FirewallConfig.objects.get(organization=self.org)
        self.assertEqual(reloaded.org_token_budget, _DEFAULT_TOKEN_BUDGET)

    def test_default_singleton_config_also_defaults(self):
        """The org-less (singleton) config defaults to 100,000 too."""
        cfg = FirewallConfig.load()
        self.assertEqual(cfg.org_token_budget, _DEFAULT_TOKEN_BUDGET)


class OrgTokenBudgetIsNumericTokenCountTests(SimpleTestCase):
    """Req 8.5 — a plain numeric token count, never a credential/secret.

    These are model/serializer introspection checks that need no database, so
    they run as a ``SimpleTestCase``.
    """

    def test_field_is_a_positive_integer_field(self):
        """The column is an integer count field, not a CharField/secret store."""
        field = FirewallConfig._meta.get_field("org_token_budget")
        self.assertIsInstance(field, django_models.PositiveIntegerField)
        # Defensive: a credential would be modelled as text, never an int field.
        self.assertNotIsInstance(field, django_models.CharField)
        self.assertNotIsInstance(field, django_models.TextField)

    def test_field_default_and_range_validators(self):
        """Default 100,000 and the declared [0, 1e9] range (Req 8.1/8.2)."""
        field = FirewallConfig._meta.get_field("org_token_budget")
        self.assertEqual(field.default, _DEFAULT_TOKEN_BUDGET)

        limits = {
            type(v): v.limit_value
            for v in field.validators
            if isinstance(v, (MinValueValidator, MaxValueValidator))
        }
        self.assertIn(MinValueValidator, limits)
        self.assertIn(MaxValueValidator, limits)
        self.assertEqual(limits[MinValueValidator], _MIN_TOKEN_BUDGET)
        self.assertEqual(limits[MaxValueValidator], _MAX_TOKEN_BUDGET)

    def test_serializer_maps_budget_to_a_plain_integer_field(self):
        """The config serializer exposes the budget as a number, not masked.

        ``FirewallConfigSerializer`` uses ``exclude`` (not an explicit field
        list), so the budget is auto-mapped from the model ``PositiveIntegerField``
        to a DRF ``IntegerField`` — a plain number on the wire. Assert it is
        present, is an integer field, and is NOT write-only / masked / a
        password-style field.
        """
        import rest_framework.serializers as drf

        fields = FirewallConfigSerializer().get_fields()
        self.assertIn("org_token_budget", fields)
        budget_field = fields["org_token_budget"]
        self.assertIsInstance(budget_field, drf.IntegerField)
        # A credential surface would be write-only (never serialized back).
        self.assertFalse(budget_field.write_only)
        # DRF CharField carries a `style` password hint for secrets; an int field
        # is never a secret input. Guard against an accidental CharField remap.
        self.assertNotIsInstance(budget_field, drf.CharField)


class OrgTokenBudgetMigrationTests(SimpleTestCase):
    """Req 8.2/8.5 — migration 0042 exists, depends on 0041, adds the field.

    Static inspection of the migration module (no database required): import
    the module, read ``Migration.dependencies`` and ``Migration.operations``.
    """

    MIGRATION = "core.migrations.0042_firewallconfig_org_token_budget"

    def _migration(self):
        return import_module(self.MIGRATION).Migration

    def test_migration_module_imports(self):
        migration = self._migration()
        self.assertTrue(hasattr(migration, "operations"))
        self.assertTrue(hasattr(migration, "dependencies"))

    def test_migration_depends_on_0041_v2plandocument(self):
        migration = self._migration()
        self.assertIn(("core", "0041_v2plandocument"), list(migration.dependencies))

    def test_migration_adds_org_token_budget_field(self):
        migration = self._migration()
        add_ops = [
            op
            for op in migration.operations
            if op.__class__.__name__ == "AddField"
            and getattr(op, "model_name", "") == "firewallconfig"
            and getattr(op, "name", "") == "org_token_budget"
        ]
        self.assertEqual(
            len(add_ops),
            1,
            "migration 0042 must add exactly one firewallconfig.org_token_budget "
            "field (task 6.2 / Requirement 8.2)",
        )
        added = add_ops[0].field
        self.assertIsInstance(added, django_models.PositiveIntegerField)
        self.assertEqual(added.default, _DEFAULT_TOKEN_BUDGET)
        limits = {
            type(v): v.limit_value
            for v in added.validators
            if isinstance(v, (MinValueValidator, MaxValueValidator))
        }
        self.assertEqual(limits.get(MinValueValidator), _MIN_TOKEN_BUDGET)
        self.assertEqual(limits.get(MaxValueValidator), _MAX_TOKEN_BUDGET)


class OrgTokenBudgetApiSerializationTests(TestCase):
    """Req 8.5 — the budget round-trips as a number through the config API."""

    def setUp(self):
        self.org = Organization.objects.create(
            name="BudgetApiOrg", slug="budget-api-org"
        )
        self.user = User.objects.create_user(
            username="budget-admin",
            email="budget@example.com",
            password="Password123!",
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_get_config_returns_budget_as_a_number(self):
        res = self.client.get(reverse("firewall-config"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("org_token_budget", res.data)
        value = res.data["org_token_budget"]
        self.assertIsInstance(value, int)
        self.assertEqual(value, _DEFAULT_TOKEN_BUDGET)

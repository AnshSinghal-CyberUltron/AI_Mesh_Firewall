"""GW27: BUILT FirewallConfig fields reach the published document; retired fields are refused.

DB-free (``SimpleTestCase``): the serializer is driven on an unsaved instance and the publish
functions are pure. The same behaviour is also exercised, by file path, from
``gateway_v2/tests/plan/test_gw27_*.py`` where the document is decoded by the real gateway
decoder and compiled.
"""
import json
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from core.firewall_config_serializer import FirewallConfigSerializer
from core.firewall_v2_controls import (
    built_value_errors,
    controls_for,
    keywords_of,
    threat_drafts,
)
from core.firewall_v2_map import FIELDS, retire_ledger, selection_errors
from core.firewall_v2_publish import document_bytes, keyword_row, policy_drafts
from core.models import FirewallConfig

_ORG = SimpleNamespace(slug="acme")


def _validate(data, **stored):
    instance = FirewallConfig(**stored)
    serializer = FirewallConfigSerializer(
        instance, data=data, partial=True, context={"organization": _ORG}
    )
    serializer.is_valid()
    return serializer.errors


class DispositionTests(SimpleTestCase):
    def test_no_unsupported_kind_remains(self):
        self.assertNotIn("unsupported", {spec["kind"] for spec in FIELDS.values()})
        # 30 on the GW27 branch; GW33 Requirement 9 maps the five factuality / grounding /
        # output_hallucination_action fields, so the merged tree retires 25.
        self.assertEqual(len(retire_ledger()), 25)

    def test_turning_rag_on_names_the_field_and_owner(self):
        errors = selection_errors({"rag_enabled": True}, {"rag_enabled": False})
        self.assertIn("rag_enabled", errors["rag_enabled"])
        self.assertIn("GW31", errors["rag_enabled"])


@override_settings(AMF_GATEWAY_BACKEND="v2")
class SerializerTests(SimpleTestCase):
    def test_a_retired_change_is_refused_with_the_field_and_owner(self):
        errors = _validate({"routing_enabled": True}, routing_enabled=False)
        self.assertIn("routing_enabled", errors)
        self.assertIn("GW28", str(errors["routing_enabled"]))

    def test_rewrite_is_refused(self):
        errors = _validate({"output_policy_action": "rewrite"})
        self.assertIn("output_policy_action", errors)

    def test_controls_backed_change_is_refused_while_the_fleet_gate_is_closed(self):
        errors = _validate({"requests_per_minute": 7}, requests_per_minute=1000)
        self.assertIn("plan schema 1", str(errors["requests_per_minute"]))

    def test_rules_backed_change_is_accepted_and_unchanged_values_pass(self):
        self.assertEqual(_validate({"blocked_keywords": "a, b"}), {})
        self.assertEqual(_validate({"requests_per_minute": 1000}, requests_per_minute=1000), {})

    def test_rate_switch_on_with_a_stored_zero_is_refused_naming_the_zero_field(self):
        with self.settings(V2_PLAN_CONTROLS_ENABLED=True, V2_PLAN_CONTROLS_ORGS="acme"):
            errors = _validate(
                {"rate_limit_enabled": True},
                rate_limit_enabled=False,
                requests_per_minute=0,
                burst_limit=5,
            )
        self.assertIn("requests_per_minute", errors)

    def test_retention_below_the_framework_floor_is_refused(self):
        with self.settings(V2_PLAN_CONTROLS_ENABLED=True, V2_PLAN_CONTROLS_ORGS="acme"):
            errors = _validate(
                {"compliance_frameworks": ["PCI-DSS"]},
                retention_days=90,
                compliance_frameworks=[],
            )
        self.assertIn("retention_days", errors)

    def test_hipaa_with_audit_off_saves(self):
        with self.settings(V2_PLAN_CONTROLS_ENABLED=True, V2_PLAN_CONTROLS_ORGS="acme"):
            errors = _validate(
                {"compliance_frameworks": ["HIPAA"], "audit_logging_enabled": False},
                retention_days=365,
                compliance_frameworks=[],
                audit_logging_enabled=True,
            )
        self.assertEqual(errors, {})


@override_settings(AMF_GATEWAY_BACKEND="v1")
class V1BackendSaveTests(SimpleTestCase):
    """While v1 serves traffic it enforces every field, so the GW27 v2 refusals must not apply."""

    def test_a_field_retired_on_v2_still_saves_on_v1(self):
        self.assertEqual(_validate({"routing_enabled": True}, routing_enabled=False), {})
        self.assertEqual(_validate({"rag_enabled": True}, rag_enabled=False), {})

    def test_a_controls_backed_change_saves_on_v1_with_the_fleet_gate_closed(self):
        self.assertEqual(_validate({"requests_per_minute": 7}, requests_per_minute=1000), {})

    def test_rewrite_stays_refused_on_v1(self):
        errors = _validate({"output_policy_action": "rewrite"})
        self.assertIn("output_policy_action", errors)


class PublishTests(SimpleTestCase):
    PAYLOAD = {
        "firewall_enabled": True,
        "content_filtering_enabled": True,
        "rate_limit_enabled": True,
        "requests_per_minute": 600,
        "burst_limit": 20,
        "audit_logging_enabled": True,
        "output_incident_logging_enabled": True,
        "retention_days": 30,
        "compliance_frameworks": ["SOC2"],
        "threat_intel_enabled": True,
        "auto_block_threats": True,
        "threat_score_threshold": 80,
        "blocked_keywords": "alpha, Alpha, beta",
    }

    def test_built_fields_reach_the_document(self):
        rows, _ = policy_drafts([keyword_row(self.PAYLOAD, "enforce")])
        body = json.loads(
            document_bytes(
                "acme", self.PAYLOAD, rows, controls_enabled=True, rules_mode="enforce"
            )
        )
        self.assertEqual(body["controls"], controls_for(self.PAYLOAD))
        self.assertEqual(body["requires"], ["controls/1"])
        ids = {rule["rule_id"] for rule in body["rules"]}
        self.assertIn("pol-fw-blocked-keywords-keywords", ids)
        self.assertEqual(len([i for i in ids if i.startswith("fw-threat-")]), 3)
        keywords = next(r for r in body["rules"] if r["rule_id"].startswith("pol-fw-blocked"))
        self.assertEqual(keywords["condition"]["keywords"], ["alpha", "beta"])

    def test_controls_are_withheld_by_default(self):
        body = json.loads(document_bytes("acme", self.PAYLOAD))
        self.assertNotIn("controls", body)

    def test_threat_drafts_off_and_keywords_helper(self):
        self.assertEqual(threat_drafts({**self.PAYLOAD, "threat_intel_enabled": False}), [])
        self.assertEqual(keywords_of(" a ,A, ,b"), ["a", "b"])
        self.assertEqual(built_value_errors({"requests_per_minute": 1}, None), {})

"""CHG-0059: gateway-envelope ingestion normalizes compliance tags onto the
ComplianceTag catalog vocabulary.

The gateway scan path emits granular tags (GDPR / HIPAA / PII / PCI-DSS / SECRET /
INFRA / SOC2); ``MCPEvent.compliance_tags`` is documented as a list of
``ComplianceTag.code`` values. ``record_mcp_event_task`` must therefore normalize
the gateway envelope's tags so the stored audit field is catalog-coded and
consistent with control-plane enforcement events.
"""

from django.test import TestCase
from unittest.mock import patch

from auth.models import Organization
from mcp_connector.models import MCPEvent
from mcp_connector.tasks import record_mcp_event_task
from policy.models import EnforcementEvent


class ComplianceTagIngestNormalizationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Vocab Org", slug="vocab-org")

    def _ingest(self, tags):
        payload = {
            "organization_id": self.org.id,
            "tool_name": "echo",
            "decision": "redact",
            "compliance_tags": tags,
        }
        event_id = record_mcp_event_task(payload)
        return MCPEvent.objects.get(id=event_id)

    def test_gateway_granular_tags_normalized_to_catalog_codes(self):
        # the exact live CHG-0017 gateway vocabulary for an email+ssn leak
        ev = self._ingest(["GDPR", "HIPAA", "PII"])
        self.assertEqual(ev.compliance_tags, ["GDPR-PII", "HIPAA-PHI"])

    def test_infra_and_secret_normalized_to_soc2_conf(self):
        ev = self._ingest(["INFRA", "SECRET"])
        self.assertEqual(ev.compliance_tags, ["SOC2-CONF"])

    def test_pci_dss_normalized_to_pci_card(self):
        ev = self._ingest(["PCI-DSS", "PII"])
        self.assertEqual(ev.compliance_tags, ["GDPR-PII", "PCI-CARD"])

    def test_already_catalog_codes_pass_through_idempotent(self):
        ev = self._ingest(["GDPR-PII", "PCI-CARD"])
        self.assertEqual(ev.compliance_tags, ["GDPR-PII", "PCI-CARD"])

    def test_empty_tags_ok(self):
        ev = self._ingest([])
        self.assertEqual(ev.compliance_tags, [])

    def test_unknown_tag_is_preserved_never_dropped(self):
        ev = self._ingest(["OWASP-MCP", "PII"])
        self.assertEqual(ev.compliance_tags, ["GDPR-PII", "OWASP-MCP"])

    @patch("mcp_connector.tasks.send_enforcement_notification")
    def test_async_ingest_mirrors_to_enforcement_and_notifies(self, notify_mock):
        payload = {
            "organization_id": self.org.id,
            "user_id": 42,
            "tool_name": "echo",
            "server_slug": "everything",
            "decision": "block",
            "request_id": "mcp-req-1",
            "metadata": {"source": "mcp_scan", "event_type": "mcp_tool_call"},
        }
        event_id = record_mcp_event_task(payload)
        self.assertTrue(MCPEvent.objects.filter(id=event_id).exists())
        mirrored = EnforcementEvent.objects.filter(
            organization=self.org,
            metadata__request_id="mcp-req-1",
            metadata__source="mcp_scan",
            metadata__tool_name="echo",
        ).first()
        self.assertIsNotNone(mirrored)
        self.assertEqual(mirrored.action, "block")
        notify_mock.assert_called_once()

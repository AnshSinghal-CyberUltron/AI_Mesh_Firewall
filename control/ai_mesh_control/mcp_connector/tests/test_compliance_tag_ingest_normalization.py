"""CHG-0059: gateway-envelope ingestion normalizes compliance tags onto the
ComplianceTag catalog vocabulary.

The gateway scan path emits granular tags (GDPR / HIPAA / PII / PCI-DSS / SECRET /
INFRA / SOC2); ``MCPEvent.compliance_tags`` is documented as a list of
``ComplianceTag.code`` values. ``record_mcp_event_task`` must therefore normalize
the gateway envelope's tags so the stored audit field is catalog-coded and
consistent with control-plane enforcement events.
"""

from django.test import TestCase

from auth.models import Organization
from mcp_connector.models import MCPEvent
from mcp_connector.tasks import record_mcp_event_task


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

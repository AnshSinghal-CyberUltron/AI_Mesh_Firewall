"""Tests for Module 2 telemetry health audit helpers."""

from django.test import SimpleTestCase, TestCase

from module2.telemetry_health import (
    ISSUE_LEGACY_MCP,
    ISSUE_NULL_ORG,
    ISSUE_ORG_KEY_MISMATCH,
    ISSUE_RAG_STAGE,
    ISSUE_UEBA_BLIND_SPOT,
    apply_metadata_fixes,
    audit_event_metadata,
    normalize_enforcement_metadata,
    repair_stale_enforcement_events,
    resolve_organization_id,
    run_telemetry_health,
)
from policy.constants import ACTION_BLOCK


class NormalizeMetadataTests(SimpleTestCase):
    def test_hoists_mcp_fields_from_extra(self):
        meta, changed = normalize_enforcement_metadata(
            {"extra": {"tools_invoked": ["echo"], "server_slug": "stub"}}
        )
        self.assertTrue(changed)
        self.assertEqual(meta["event_type"], "mcp_tool_call")
        self.assertEqual(meta["tools_invoked"], ["echo"])

    def test_backfills_rag_event_type(self):
        meta, changed = normalize_enforcement_metadata({"pipeline_stage": "retriever"})
        self.assertTrue(changed)
        self.assertEqual(meta["event_type"], "rag_pipeline")

    def test_idempotent_when_already_normalized(self):
        meta, changed = normalize_enforcement_metadata(
            {"event_type": "mcp_tool_call", "tools_invoked": ["x"], "server_slug": "s"}
        )
        self.assertFalse(changed)


class ResolveOrganizationTests(SimpleTestCase):
    def test_prefers_payload_organization_id(self):
        org_id = resolve_organization_id({"organization_id": 5}, {}, {})
        self.assertEqual(org_id, 5)

    def test_falls_back_to_key_prefix_map(self):
        org_id = resolve_organization_id({}, {"key_prefix": "abc123"}, {"abc123": 2})
        self.assertEqual(org_id, 2)


class TelemetryHealthAuditTests(SimpleTestCase):
    def test_legacy_mcp_missing_event_type(self):
        issues = audit_event_metadata(
            1,
            10,
            {"tools_invoked": ["echo"], "server_slug": "stub"},
            {},
        )
        codes = {i.code for i in issues}
        self.assertIn(ISSUE_LEGACY_MCP, codes)

    def test_org_key_mismatch(self):
        issues = audit_event_metadata(
            2,
            1,
            {"key_prefix": "abc123", "model": "gpt-4o"},
            {"abc123": 2},
        )
        self.assertTrue(any(i.code == ISSUE_ORG_KEY_MISMATCH for i in issues))

    def test_rag_stage_without_event_type(self):
        issues = audit_event_metadata(
            3,
            1,
            {"pipeline_stage": "retriever"},
            {},
        )
        self.assertTrue(any(i.code == ISSUE_RAG_STAGE for i in issues))

    def test_null_organization(self):
        issues = audit_event_metadata(4, None, {"model": "gpt-4o"}, {})
        self.assertTrue(any(i.code == ISSUE_NULL_ORG for i in issues))

    def test_ueba_blind_spot_without_key_prefix(self):
        issues = audit_event_metadata(
            5,
            1,
            {"model": "gpt-4o", "threat_type": "prompt_injection"},
            {},
        )
        self.assertTrue(any(i.code == ISSUE_UEBA_BLIND_SPOT for i in issues))


class TelemetryHealthCommandTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Health Org", slug="health-org")

    def _event(self, **meta):
        from policy.models import EnforcementEvent

        return EnforcementEvent.objects.create(
            organization=self.org,
            action=ACTION_BLOCK,
            metadata=meta,
        )

    def test_run_telemetry_health_detects_legacy_mcp(self):
        self._event(tools_invoked=["read_file"], server_slug="stub")
        report = run_telemetry_health(
            self.org.enforcement_events.all(),
            period="24h",
            organization_slug=self.org.slug,
        )
        self.assertEqual(report.issue_counts[ISSUE_LEGACY_MCP], 1)
        self.assertIn(report.remediable_event_ids[0], report.sample_event_ids[ISSUE_LEGACY_MCP])

    def test_apply_metadata_fixes_backfills_event_type(self):
        ev = self._event(tools_invoked=["echo"], server_slug="stub")
        stats = apply_metadata_fixes([ev.id], dry_run=False)
        self.assertEqual(stats["updated"], 1)
        ev.refresh_from_db()
        self.assertEqual(ev.metadata.get("event_type"), "mcp_tool_call")
        self.assertIn("telemetry_health_repaired_at", ev.metadata)

    def test_repair_stale_enforcement_events_fixes_legacy_rows(self):
        from core.models import GatewayAPIKey
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(username="repair-user", email="repair@test.io", password="x")
        key, _ = GatewayAPIKey.generate_key(
            name="repair-key",
            owner=user,
            project_id="repair",
        )
        key.organization = self.org
        key.save(update_fields=["organization"])

        ev = self._event(
            tools_invoked=["read_file"],
            server_slug="stub",
            key_prefix=key.prefix,
        )
        ev.organization_id = None
        ev.save(update_fields=["organization_id"])

        stats = repair_stale_enforcement_events(lookback_hours=24, batch_size=50)
        self.assertGreaterEqual(stats["metadata_updated"], 1)
        self.assertGreaterEqual(stats["org_updated"], 1)

        ev.refresh_from_db()
        self.assertEqual(ev.metadata.get("event_type"), "mcp_tool_call")
        self.assertEqual(ev.organization_id, self.org.id)

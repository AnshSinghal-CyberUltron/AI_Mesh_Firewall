"""SECURE-BY-DEFAULT: a new MCP server defaults to 'redact', not observe-only 'tag'.

Real incident (2026-07-22): github-mcp was on the old 'tag' default, so a detected
AWS credential in issue_write arguments egressed raw into a public GitHub issue —
tag/monitor detect + tag but never mutate/block. New servers now mask on egress out
of the box; the operator can still lower a specific server to 'tag' explicitly, and
this change is NOT retroactive (existing servers keep their chosen posture).
"""
from django.test import TestCase

from mcp_connector.models import MCPServerRegistration


class SecureDefaultPostureTests(TestCase):
    def test_field_default_is_redact(self):
        self.assertEqual(
            MCPServerRegistration._meta.get_field("default_scan_action").default,
            "redact",
        )

    def test_new_instance_resolves_redact(self):
        self.assertEqual(MCPServerRegistration().default_scan_action, "redact")

    def test_tag_still_selectable(self):
        choices = dict(MCPServerRegistration._meta.get_field("default_scan_action").choices)
        self.assertIn("tag", choices)
        self.assertIn("block", choices)

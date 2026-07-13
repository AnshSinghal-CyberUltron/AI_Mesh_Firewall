"""MCP_HOST_TOOLS validation on server registration."""

from django.test import TestCase

from mcp_connector.serializers import MCPServerCreateSerializer


class HostToolsEnvValidationTests(TestCase):
    def _errors(self, data):
        ser = MCPServerCreateSerializer(data=data)
        ok = ser.is_valid()
        return ok, ser.errors

    def test_valid_mcp_host_tools_accepted(self):
        ok, errors = self._errors(
            {
                "name": "host-tools-ok",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "mcp-server-semgrep"],
                "env_vars": {"MCP_HOST_TOOLS": "pip:semgrep"},
            }
        )
        self.assertTrue(ok, msg=f"expected valid, got {errors}")

    def test_invalid_manager_rejected(self):
        ok, errors = self._errors(
            {
                "name": "host-tools-bad-mgr",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "pkg"],
                "env_vars": {"MCP_HOST_TOOLS": "gem:semgrep"},
            }
        )
        self.assertFalse(ok)
        self.assertIn("MCP_HOST_TOOLS", errors)

    def test_invalid_package_name_rejected(self):
        ok, errors = self._errors(
            {
                "name": "host-tools-bad-pkg",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "pkg"],
                "env_vars": {"MCP_HOST_TOOLS": "pip:evil;rm"},
            }
        )
        self.assertFalse(ok)
        self.assertIn("MCP_HOST_TOOLS", errors)

    def test_host_tools_list_merged_into_env_vars(self):
        ser = MCPServerCreateSerializer(
            data={
                "name": "host-tools-list",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "pkg"],
                "host_tools": ["pip:semgrep", "npm:eslint"],
            }
        )
        self.assertTrue(ser.is_valid(), msg=f"expected valid, got {ser.errors}")
        from ai_mesh_shared.mcp_host_tools import MCP_HOST_TOOLS_ENV_KEY

        self.assertEqual(
            ser.validated_data["env_vars"][MCP_HOST_TOOLS_ENV_KEY],
            "semgrep npm:eslint",
        )

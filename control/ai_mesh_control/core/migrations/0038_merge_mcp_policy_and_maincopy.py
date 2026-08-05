# Merge parallel core leaf nodes on main-copy:
#   0037_merge_20260630_0645 (main ↔ module2 gateway key purpose)
#   0037_firewallconfig_mcp_policy_only_enforcement (MCP policy-only flag)

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_merge_20260630_0645"),
        ("core", "0037_firewallconfig_mcp_policy_only_enforcement"),
    ]

    operations = []

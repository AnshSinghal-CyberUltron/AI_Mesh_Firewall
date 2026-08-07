"""Merge parallel ``core`` leaf nodes on ``main-copy``.

What this migration does
------------------------
Empty merge only (``operations = []``). No columns are added or altered.

It joins two ``0037_*`` leaves that diverged on this branch:

  * ``0037_merge_20260630_0645`` — main ↔ Module 2 graph merge
    (UEBA risk_score path; no ``key_purpose`` schema change)
  * ``0037_firewallconfig_mcp_policy_only_enforcement`` — MCP
    policy-only enforcement flag on FirewallConfig

Without this node Django again has two ``core`` heads after both 0037
migrations apply.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_merge_20260630_0645"),
        ("core", "0037_firewallconfig_mcp_policy_only_enforcement"),
    ]

    operations = []

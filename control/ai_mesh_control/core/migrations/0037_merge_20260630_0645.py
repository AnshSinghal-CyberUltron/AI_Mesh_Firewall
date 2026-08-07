"""Merge parallel ``core`` heads after main ↔ Module 2 integration.

What this migration does
------------------------
Empty merge only (``operations = []``). No columns are added or altered.

It joins two branches that both existed after the Module 2 PR landed beside
main:

  * main line:     ``0035_alter_firewallconfig_rag_redaction_default``
  * Module 2 line: ``0035_merge_20260629_0959`` → ``0036`` (UEBA ``risk_score``
    help-text / validators only — ``key_purpose`` is NOT part of that alter)

Without this node Django sees two ``core`` heads and ``migrate`` cannot
pick a single leaf.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_alter_firewallconfig_rag_redaction_default"),
        ("core", "0036_alter_gatewayapikey_key_purpose_and_more"),
    ]

    operations = []

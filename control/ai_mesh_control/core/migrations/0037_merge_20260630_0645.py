# Merge parallel core branches after main → module2 integration:
#   main:     0035_alter_firewallconfig_rag_redaction_default
#   module2:  0035_merge_20260629_0959 → 0036_alter_gatewayapikey_key_purpose_and_more

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_alter_firewallconfig_rag_redaction_default"),
        ("core", "0036_alter_gatewayapikey_key_purpose_and_more"),
    ]

    operations = []

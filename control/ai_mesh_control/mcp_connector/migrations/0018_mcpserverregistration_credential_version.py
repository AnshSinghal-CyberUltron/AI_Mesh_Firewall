"""GW17b: credential_version, the compare-and-set token for gateway refresh write-back."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mcp_connector", "0017_alter_mcpserverregistration_default_scan_action"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverregistration",
            name="credential_version",
            field=models.PositiveIntegerField(default=1),
        ),
    ]

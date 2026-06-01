"""Unify guardrails into the policy engine; remove the legacy Enkrypt layer.

- Deletes the inert ``GuardrailProfile`` model (Enkrypt Secure-MCP-Gateway
  profile store). It was never on the tools/call hot path — only the policy
  engine enforces — so removing it ends the confusing policy/guardrail
  duality on the firewall-1-4 surface. Reversible: re-adds the model on
  downgrade.
- Adds ``needs_reauth`` to MCPServerRegistration so the UI can surface an
  actionable per-org "re-authenticate <server>" badge when outbound auth
  (OAuth refresh / credentials) can no longer be established, without the
  hot path having to raise.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0009_oauth_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpserverregistration",
            name="needs_reauth",
            field=models.BooleanField(default=False),
        ),
        migrations.DeleteModel(
            name="GuardrailProfile",
        ),
    ]

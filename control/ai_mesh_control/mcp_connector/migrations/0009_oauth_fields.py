"""Phase C: OAuth 2.1 authorization fields on MCPServerRegistration.

Adds the columns needed to drive the MCP OAuth flow (2025-06-18 spec):
RFC 9728 PRM → RFC 8414 AS metadata → RFC 7591 DCR → PKCE authorization-code
with the RFC 8707 ``resource`` parameter. The obtained access token is stored
in the existing encrypted ``auth_token`` and forwarded to the gateway as a
normal bearer, so the gateway data plane needs no OAuth awareness.

Secrets (oauth_client_secret / oauth_refresh_token / oauth_code_verifier) use
EncryptedCharField (Fernet at rest, "enc:" prefix). All other columns are plain
varchar/datetime. These are metadata-only AddFields (constant defaults, PG11+
adds columns without a table rewrite). The AlterField on ``auth_type`` only
extends the choice list (no SQL change).
"""
from django.db import migrations, models

import policy.encrypted_fields


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_connector", "0008_byok_auth_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mcpserverregistration",
            name="auth_type",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("none", "None"),
                    ("bearer", "Bearer token"),
                    ("basic", "Basic auth"),
                    ("authheaders", "Custom header"),
                    ("query_param", "Query parameter"),
                    ("oauth", "OAuth 2.1"),
                ],
                default="none",
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_authorization_endpoint",
            field=models.CharField(blank=True, default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_token_endpoint",
            field=models.CharField(blank=True, default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_registration_endpoint",
            field=models.CharField(blank=True, default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_client_id",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_client_secret",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True, default="", max_length=2048
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_scope",
            field=models.CharField(blank=True, default="", max_length=2048),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_resource",
            field=models.CharField(blank=True, default="", max_length=1024),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_refresh_token",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True, default="", max_length=4096
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_token_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_code_verifier",
            field=policy.encrypted_fields.EncryptedCharField(
                blank=True, default="", max_length=512
            ),
        ),
        migrations.AddField(
            model_name="mcpserverregistration",
            name="oauth_state",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=128
            ),
        ),
    ]

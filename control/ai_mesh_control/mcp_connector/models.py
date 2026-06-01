import uuid

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from policy.encrypted_fields import EncryptedCharField


class MCPServerRegistration(models.Model):
    """Tracks MCP servers registered locally for gateway proxy/exec.

    DECISION-D Phase 0: ContextForge was removed; the gateway is the sole
    proxy/exec layer for every transport (streamable-http, sse, stdio,
    websocket). The historical ``contextforge_server_id`` column was dropped
    in migration 0006.
    """

    CONNECTION_STATUS_CHOICES = [
        ("connected", "Connected"),
        ("failed", "Failed"),
        ("syncing", "Syncing"),
        ("unknown", "Unknown"),
    ]

    RISK_LEVEL_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    server_slug = models.SlugField(
        max_length=128,
        blank=True,
        default="",
        help_text="URL-safe identifier for gateway endpoint; auto-generated from name",
    )
    url = models.URLField(
        help_text="MCP server endpoint URL (required for http/sse/websocket, blank for stdio)",
        blank=True,
        default="",
    )
    transport = models.CharField(
        max_length=32,
        default="streamable-http",
        choices=[
            ("streamable-http", "Streamable HTTP"),
            ("sse", "SSE"),
            ("stdio", "Stdio"),
            ("websocket", "WebSocket"),
        ],
    )
    # Stdio transport fields
    command = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Executable command for stdio transport (e.g. npx, python, node)",
    )
    args = models.JSONField(
        default=list,
        blank=True,
        help_text="Command arguments for stdio transport as a JSON array",
    )
    env_vars = models.JSONField(
        default=dict,
        blank=True,
        help_text="Environment variables for stdio transport as a JSON object",
    )
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_exposed_to_agents = models.BooleanField(
        default=True,
        help_text="Whether this server is accessible via the external gateway endpoint",
    )
    connection_status = models.CharField(
        max_length=16,
        choices=CONNECTION_STATUS_CHOICES,
        default="unknown",
    )
    tools_count = models.PositiveIntegerField(default=0)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_health_at = models.DateTimeField(null=True, blank=True)
    last_health_status = models.CharField(
        max_length=16,
        choices=[
            ("healthy", "Healthy"),
            ("unhealthy", "Unhealthy"),
            ("unreachable", "Unreachable"),
        ],
        default="unreachable",
    )
    risk_level = models.CharField(
        max_length=16,
        choices=RISK_LEVEL_CHOICES,
        default="low",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="mcp_servers",
        null=True,
        blank=True,
    )
    # DECISION-D Phase 1: default Presidio action applied to every tool on this
    # server unless the tool overrides via MCPToolRegistration.presidio_action.
    default_presidio_action = models.CharField(
        max_length=8,
        choices=[("tag", "Tag only"), ("redact", "Redact"), ("block", "Block")],
        default="tag",
    )

    # ── BYOK auth (Phase B) ──────────────────────────────────────────
    # Client-supplied credentials for outbound auth to the upstream MCP
    # server. Secrets are stored encrypted-at-rest via EncryptedCharField
    # (transparent Fernet, "enc:" prefix). The encryption key is BYOK:
    # settings.FIELD_ENCRYPTION_KEY (env, not persisted) with SECRET_KEY
    # fallback for boot-safety. These are never serialized to API responses
    # (serializer marks the corresponding inputs write_only).
    AUTH_TYPE_CHOICES = [
        ("none", "None"),
        ("bearer", "Bearer token"),
        ("basic", "Basic auth"),
        ("authheaders", "Custom header"),
        ("query_param", "Query parameter"),
        ("oauth", "OAuth 2.1"),
    ]
    auth_type = models.CharField(
        max_length=16,
        choices=AUTH_TYPE_CHOICES,
        default="none",
    )
    auth_token = EncryptedCharField(max_length=2048, blank=True, default="")
    auth_username = EncryptedCharField(max_length=512, blank=True, default="")
    auth_password = EncryptedCharField(max_length=2048, blank=True, default="")
    auth_header_key = models.CharField(max_length=128, blank=True, default="")
    auth_header_value = EncryptedCharField(max_length=2048, blank=True, default="")

    # ── OAuth 2.1 (Phase C) ──────────────────────────────────────────
    # MCP authorization per the 2025-06-18 spec: RFC 9728 protected-resource
    # metadata → RFC 8414 AS metadata → RFC 7591 dynamic client registration →
    # PKCE authorization-code flow with the RFC 8707 ``resource`` parameter.
    # The obtained ACCESS token is stored in ``auth_token`` above (encrypted)
    # and forwarded to the gateway as a normal bearer — so the gateway data
    # plane needs no OAuth awareness. The columns below hold the OAuth state
    # needed to acquire and refresh that token. Secrets (client_secret,
    # refresh_token, code_verifier) use EncryptedCharField (Fernet, BYOK key).
    oauth_authorization_endpoint = models.CharField(max_length=1024, blank=True, default="")
    oauth_token_endpoint = models.CharField(max_length=1024, blank=True, default="")
    oauth_registration_endpoint = models.CharField(max_length=1024, blank=True, default="")
    oauth_client_id = models.CharField(max_length=512, blank=True, default="")
    oauth_client_secret = EncryptedCharField(max_length=2048, blank=True, default="")
    oauth_scope = models.CharField(max_length=2048, blank=True, default="")
    oauth_resource = models.CharField(max_length=1024, blank=True, default="")
    oauth_refresh_token = EncryptedCharField(max_length=4096, blank=True, default="")
    oauth_token_expires_at = models.DateTimeField(null=True, blank=True)
    # Transient per-flow values (cleared on callback completion):
    oauth_code_verifier = EncryptedCharField(max_length=512, blank=True, default="")
    oauth_state = models.CharField(max_length=128, blank=True, default="", db_index=True)

    # ── Sync diagnostics (Phase A surfaced these; columns added Phase B) ──
    last_sync_error = models.TextField(blank=True, default="")
    last_sync_attempt_at = models.DateTimeField(null=True, blank=True)
    # Set when outbound auth to the upstream MCP server can no longer be
    # established (OAuth refresh failed, credentials missing). The hot path
    # stays graceful (no 500); the UI surfaces an actionable per-org
    # "re-authenticate <server>" badge from this flag + last_sync_error.
    needs_reauth = models.BooleanField(default=False)

    @property
    def oauth_authorized(self) -> bool:
        """True when a usable OAuth access token has been obtained."""
        return self.auth_type == "oauth" and bool(self.auth_token)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "server_slug"],
                name="unique_org_server_slug",
            ),
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_org_server_name",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.server_slug:
            self.server_slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def gateway_endpoint(self):
        """Deterministic external gateway endpoint for this server."""
        org = self.organization
        if org and self.server_slug:
            endpoint_path = f"/gateway/{org.slug}/mcp/{self.server_slug}"
            gateway_base = (getattr(settings, "GATEWAY_PUBLIC_URL", "") or "").strip().rstrip("/")
            if gateway_base:
                return f"{gateway_base}{endpoint_path}"
            return endpoint_path
        return ""

    def __str__(self):
        return f"{self.name} ({self.url})"


class MCPToolRegistration(models.Model):
    """Per-tool inventory for an MCP server — supports enable/disable and sensitivity labels."""

    SENSITIVITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    server = models.ForeignKey(
        MCPServerRegistration,
        on_delete=models.CASCADE,
        related_name="tool_registrations",
    )
    tool_name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=True)
    sensitivity = models.CharField(
        max_length=16,
        choices=SENSITIVITY_CHOICES,
        default="low",
    )
    input_schema = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="mcp_tools",
        null=True,
        blank=True,
    )
    # DECISION-D Phase 1: per-tool Presidio action override. ``inherit`` defers
    # to the parent server's ``default_presidio_action``.
    presidio_action = models.CharField(
        max_length=8,
        choices=[
            ("inherit", "Inherit from server"),
            ("tag", "Tag only"),
            ("redact", "Redact"),
            ("block", "Block"),
        ],
        default="inherit",
    )

    class Meta:
        ordering = ["tool_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["server", "tool_name"],
                name="unique_server_tool",
            ),
        ]

    def __str__(self):
        return f"{self.tool_name} @ {self.server.name}"


class MCPEvent(models.Model):
    """Structured observability event for MCP tool calls — not raw logs."""

    DECISION_CHOICES = [
        ("allow", "Allow"),
        ("block", "Block"),
        ("redact", "Redact"),
        ("error", "Error"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="mcp_events",
        null=True,
        blank=True,
    )
    user_id = models.IntegerField(null=True, blank=True)
    username = models.CharField(max_length=255, blank=True, default="")
    server_slug = models.CharField(max_length=128, blank=True, default="")
    server_name = models.CharField(max_length=255, blank=True, default="")
    tool_name = models.CharField(max_length=255)
    decision = models.CharField(max_length=16, choices=DECISION_CHOICES, default="allow")
    policy_ids = models.JSONField(default=list, blank=True)
    policy_reason = models.TextField(blank=True, default="")
    latency_ms = models.IntegerField(default=0)
    request_id = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    # DECISION-D Phase 1: Presidio + ComplianceTag annotations.
    # ``compliance_tags`` is a sorted list of ComplianceTag.code values
    # (e.g. ["GDPR-PII", "PCI-CARD"]). ``presidio_findings`` is a list of
    # {entity_type, score, start, end, direction} dicts captured at scan time.
    compliance_tags = models.JSONField(default=list, blank=True)
    presidio_findings = models.JSONField(default=list, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["organization", "-timestamp"]),
            models.Index(fields=["decision"]),
            models.Index(fields=["tool_name"]),
        ]

    def __str__(self):
        return f"{self.decision}: {self.tool_name} @ {self.timestamp}"

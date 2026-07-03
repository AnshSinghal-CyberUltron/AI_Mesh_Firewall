import uuid

from django.conf import settings
from django.core.validators import MaxLengthValidator
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
    url = models.CharField(
        max_length=2048,
        blank=True,
        default="",
        validators=[MaxLengthValidator(2048)],
        help_text="MCP server endpoint URL (http/https/ws/wss for remote; blank for stdio)",
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
    # Default scan enforcement action applied to every tool on this server
    # unless the tool overrides via MCPToolRegistration.scan_action. Drives the
    # Tier-1/Tier-2 scan outcome (tag = observe only, redact, block). Formerly
    # ``default_presidio_action``; Presidio is removed and detection is now
    # engine-agnostic (regex Tier-1 + Bedrock Tier-2).
    default_scan_action = models.CharField(
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
            # Derive a UNIQUE slug from the name. The slug drives the export URL
            # (gateway_endpoint = /gateway/<org>/mcp/<slug>), which must never collide.
            # A distinctly-named server whose name merely slugifies to a taken slug
            # (e.g. "Playwright!!!" -> "playwright") is auto-suffixed to "playwright-2"
            # so it registers cleanly with its own export URL, instead of failing with
            # a misleading "name already exists". Same-NAME duplicates are still
            # rejected upstream by the (organization, name) unique constraint.
            base = slugify(self.name) or "mcp-server"
            slug = base
            if self.organization_id:
                taken = set(
                    type(self).objects
                    .filter(organization_id=self.organization_id)
                    .exclude(pk=self.pk)
                    .values_list("server_slug", flat=True)
                )
                n = 2
                while slug in taken:
                    slug = f"{base}-{n}"
                    n += 1
            self.server_slug = slug
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
    # Per-tool scan enforcement override. ``inherit`` defers to the parent
    # server's ``default_scan_action``. Formerly ``presidio_action``.
    scan_action = models.CharField(
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
        ("monitor", "Monitor"),
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
    # Scan annotations. ``compliance_tags`` is a sorted list of ComplianceTag.code
    # values (e.g. ["GDPR-PII", "PCI-CARD"]). ``scan_findings`` is a list of
    # {entity_type, score, start, end, direction, tier, threat_type, detail} dicts
    # captured at scan time (Tier-1 regex + Tier-2 Bedrock). Formerly
    # ``presidio_findings``.
    compliance_tags = models.JSONField(default=list, blank=True)
    scan_findings = models.JSONField(default=list, blank=True)
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


class MCPScanControl(models.Model):
    """Per-org MCP scan control matrix (Tier-1 static / Tier-2 Bedrock).

    Additive to the per-server/per-tool ``default_scan_action`` / ``scan_action``
    enforcement fields.
    The gateway resolves effective controls per tool call using precedence:
    tool → server → org (then ``priority`` within the same scope level).
    """

    TIER_CHOICES = [
        ("tier1", "Tier 1 (static)"),
        ("tier2", "Tier 2 (Bedrock)"),
    ]
    DIRECTION_CHOICES = [
        ("input", "Input"),
        ("output", "Output"),
        ("both", "Both"),
    ]
    SCOPE_CHOICES = [
        ("org", "Organization"),
        ("server", "Server"),
        ("tool", "Tool"),
    ]
    TARGET_CHOICES = [
        ("entire", "Entire payload"),
        ("key_path", "Key path"),
    ]
    STRICT_CHOICES = [
        ("strict", "Strict (fail closed)"),
        ("fail_open", "Fail open (degraded pass)"),
    ]
    ACTION_CHOICES = [
        ("inherit", "Inherit from server/tool default"),
        ("monitor", "Monitor (detect + tag, allow)"),
        ("redact", "Redact"),
        ("block", "Block"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="mcp_scan_controls",
    )
    server = models.ForeignKey(
        MCPServerRegistration,
        on_delete=models.CASCADE,
        related_name="scan_controls",
        null=True,
        blank=True,
    )
    tool_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Required when scope_type=tool; must match MCPToolRegistration.tool_name.",
    )
    tier = models.CharField(max_length=8, choices=TIER_CHOICES)
    enabled = models.BooleanField(default=True)
    direction = models.CharField(
        max_length=8,
        choices=DIRECTION_CHOICES,
        default="both",
    )
    scope_type = models.CharField(
        max_length=8,
        choices=SCOPE_CHOICES,
        default="org",
    )
    target_mode = models.CharField(
        max_length=16,
        choices=TARGET_CHOICES,
        default="entire",
    )
    key_path = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Dot path (e.g. arguments.email) or simple key name for key_path mode.",
    )
    strict_mode = models.CharField(
        max_length=16,
        choices=STRICT_CHOICES,
        default="fail_open",
        help_text="Tier-2 degradation behaviour when Bedrock is unavailable.",
    )
    action = models.CharField(
        max_length=8,
        choices=ACTION_CHOICES,
        default="inherit",
        help_text=(
            "Enforcement action for THIS tier+direction+scope row, independent "
            "per tier. 'inherit' defers to MCPToolRegistration.scan_action then "
            "MCPServerRegistration.default_scan_action then 'monitor' (safe "
            "observe-only default). Precedence within a request: block > redact "
            "> monitor; a Tier-1 block short-circuits Tier-2 (Bedrock never runs)."
        ),
    )
    priority = models.IntegerField(
        default=100,
        help_text="Higher wins within the same scope_type + tier + direction.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "tier", "direction"]
        indexes = [
            models.Index(fields=["organization", "tier", "enabled"]),
            models.Index(fields=["server", "tool_name"]),
        ]

    def __str__(self) -> str:
        scope = self.scope_type
        if self.tool_name:
            scope = f"tool:{self.tool_name}"
        elif self.server_id:
            scope = f"server:{self.server_id}"
        return f"{self.tier}/{self.direction}/{self.action}@{scope}"

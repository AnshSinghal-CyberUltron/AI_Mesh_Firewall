import uuid

from django.conf import settings
from django.db import models
from django.utils.text import slugify


class MCPServerRegistration(models.Model):
    """Tracks MCP servers registered via AISecShield → ContextForge."""

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
    contextforge_server_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="ID assigned by ContextForge after registration",
    )
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


class GuardrailProfile(models.Model):
    """Reusable guardrail policy profile stored in AISecShield
    and synced to Secure-MCP-Gateway on apply."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="guardrail_profiles",
        null=True,
        blank=True,
    )
    mcp_server = models.ForeignKey(
        MCPServerRegistration,
        on_delete=models.SET_NULL,
        related_name="guardrail_profiles",
        null=True,
        blank=True,
        help_text="Optional: bind this profile to a specific MCP server",
    )
    description = models.TextField(blank=True, default="")
    input_policy = models.JSONField(
        default=dict,
        blank=True,
        help_text="Input guardrail policy (PII redaction, injection blocking, etc.)",
    )
    output_policy = models.JSONField(
        default=dict,
        blank=True,
        help_text="Output guardrail policy (adherence, relevancy, etc.)",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="If true, automatically applied to newly registered MCP servers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

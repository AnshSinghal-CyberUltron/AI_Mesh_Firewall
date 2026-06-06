from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone


class Policy(models.Model):
    """Policy container; holds rules and metadata."""

    SEVERITY_CHOICES = [
        ("CRITICAL", "Critical"),
        ("HIGH", "High"),
        ("MEDIUM", "Medium"),
        ("LOW", "Low"),
    ]
    DOMAIN_CHOICES = [
        ("pipeline", "Pipeline"),
        ("rag", "RAG"),
        ("mcp", "MCP"),
    ]
    name = models.CharField(max_length=255)
    code = models.SlugField(max_length=64, unique=True, help_text="Unique identifier (e.g. POL001)")
    policy_domain = models.CharField(
        max_length=16,
        choices=DOMAIN_CHOICES,
        default="pipeline",
        db_index=True,
        help_text="Enforcement domain: pipeline, rag, or mcp",
    )
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="policies",
    )
    mcp_server = models.ForeignKey(
        "mcp_connector.MCPServerRegistration",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="policies",
        help_text="Optional: bind this policy to a specific MCP server (MCP-domain only)",
    )
    category = models.CharField(max_length=128, blank=True)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default="MEDIUM")
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, help_text="System-seeded policy; cannot be deleted via API")
    priority = models.PositiveIntegerField(default=0, help_text="Higher = evaluated first")
    metadata = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1, help_text="Incremented on each update for conflict detection")
    # ── G7: response field redaction allowlist ───────────────────────────
    # Field names listed here will be replaced with "[REDACTED]" in MCP
    # tool-call responses whenever this policy matches. Case-insensitive
    # exact key match (NFKC-normalized) is applied recursively to dict/list
    # values returned by the tool. Empty list = no field redaction.
    redaction_fields = ArrayField(
        models.CharField(max_length=128),
        default=list,
        blank=True,
        help_text="Top-level / nested dict keys to redact in tool responses (case-insensitive exact match).",
    )
    # ── G8: per-user / per-agent / per-role allowlist ────────────────────
    # Empty list = wildcard (policy applies to everyone). Non-empty list =
    # strict allowlist (policy only matches when the request's actor is in
    # the list). actor_user_id matches allowed_user_ids; the calling API
    # key prefix matches allowed_agent_ids; the user's profile roles must
    # overlap allowed_roles.
    allowed_user_ids = ArrayField(
        models.IntegerField(),
        default=list,
        blank=True,
        help_text="Empty = any user. Non-empty = only these user IDs are subject to this policy.",
    )
    allowed_agent_ids = ArrayField(
        models.CharField(max_length=128),
        default=list,
        blank=True,
        help_text="Empty = any agent. Non-empty = only these agent IDs (API key prefix) are subject to this policy.",
    )
    allowed_roles = ArrayField(
        models.CharField(max_length=64),
        default=list,
        blank=True,
        help_text="Empty = any role. Non-empty = only users with at least one of these role names are subject to this policy.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "code"]
        verbose_name_plural = "Policies"

    def __str__(self):
        return f"{self.code} ({self.name})"


class Rule(models.Model):
    """Single rule under a policy; condition + action."""

    RULE_TYPE_CHOICES = [
        ("regex", "Regex"),
        ("keywords", "Keywords"),
        ("pattern", "Pattern"),
    ]
    ACTION_CHOICES = [
        ("block", "Block"),
        ("redact", "Redact"),
        ("monitor", "Monitor"),
        ("rewrite", "Rewrite"),
        ("model_downgrade", "Model Downgrade"),
    ]
    PIPELINE_STAGE_CHOICES = [
        ("", "All Stages"),
        ("query", "Query Stage"),
        ("retriever", "Retriever Stage"),
        ("ranker", "Ranker Stage"),
        ("generator", "Generator Stage"),
    ]
    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="rules")
    name = models.CharField(max_length=255)
    rule_type = models.CharField(max_length=32, choices=RULE_TYPE_CHOICES, default="keywords")
    condition = models.JSONField(default=dict, help_text='e.g. {"regex": "...", "keywords": [...], "field": "prompt"}')
    action = models.CharField(max_length=24, choices=ACTION_CHOICES, default="block")
    redaction_config = models.JSONField(default=dict, blank=True, help_text="For redact: replacement text, patterns")
    priority = models.PositiveIntegerField(default=0, help_text="Higher = evaluated first")
    enabled = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    pipeline_stage = models.CharField(
        max_length=16,
        choices=PIPELINE_STAGE_CHOICES,
        default="",
        blank=True,
        db_index=True,
        help_text="Pipeline stage this rule targets (empty = all stages)",
    )
    target_tool = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="MCP tool name this rule targets (empty = all tools). Only used for MCP-domain policies.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["policy", "-priority", "id"]

    def __str__(self):
        return f"{self.policy.code}/{self.name} ({self.action})"


class EnforcementEvent(models.Model):
    """Record of policy enforcement (block/redact/monitor) for SOC and dashboards."""

    INCIDENT_STATUS_CHOICES = [
        ("investigating", "Investigating"),
        ("escalated", "Escalated"),
        ("resolved", "Resolved"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enforcement_events",
        db_index=True,
    )
    policy = models.ForeignKey(Policy, on_delete=models.SET_NULL, null=True, related_name="enforcement_events")
    rule = models.ForeignKey(Rule, on_delete=models.SET_NULL, null=True, related_name="enforcement_events")
    action = models.CharField(max_length=16)  # block, redact, monitor
    user_id = models.IntegerField(null=True, blank=True)
    endpoint_id = models.IntegerField(null=True, blank=True)
    agent = models.ForeignKey(
        "core.Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enforcement_events",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    # Phase-0 CC-1: discriminator for operational event streams.
    # Free-form string (no `choices=`) because the gateway is the source of
    # truth for event-class values and may extend them independently. See
    # gateway/ai_mesh_gateway/telemetry_ops.py:KNOWN_EVENT_CLASSES.
    # Default backfills pre-existing rows as plain enforcement events.
    event_class = models.CharField(
        max_length=64,
        default="enforcement",
        db_index=True,
        help_text=(
            "Operational event discriminator. Gateway-owned protocol "
            "constant (see gateway/ai_mesh_gateway/telemetry_ops.py)."
        ),
    )

    # Incident lifecycle fields for MTTR tracking
    incident_status = models.CharField(
        max_length=16,
        choices=INCIDENT_STATUS_CHOICES,
        default="investigating",
        db_index=True,
    )
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalated_by_id = models.IntegerField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Phase-0 CC-1: composite covering the realistic SOC query
            # `WHERE organization=X AND event_class=Y ORDER BY created_at DESC`.
            # Org-first per triage Agent-B: high-cardinality filter eliminates
            # the most rows first.
            models.Index(
                fields=["organization", "event_class", "-created_at"],
                name="ev_org_evclass_ts_idx",
            ),
        ]

    def __str__(self):
        return f"{self.action} policy={self.policy_id} rule={self.rule_id} @ {self.created_at}"


class Notification(models.Model):
    """Persisted notification for admin users, e.g. escalation requests."""

    TYPE_CHOICES = [
        ("escalation", "Escalation"),
        ("resolution", "Resolution"),
    ]

    type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    enforcement_event = models.ForeignKey(
        EnforcementEvent,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    recipient_id = models.IntegerField(db_index=True)
    message = models.CharField(max_length=512, blank=True)
    read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type} → user {self.recipient_id} event={self.enforcement_event_id}"


class PolicyVersion(models.Model):
    """Snapshot of a policy (and its rules) at a version; for history and conflict resolution."""

    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict, help_text="Policy + rules snapshot")
    comment = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="policy_versions_created",
    )

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(fields=["policy", "version"], name="policy_version_unique"),
        ]

    def __str__(self):
        return f"{self.policy.code} v{self.version} @ {self.created_at}"


class ComplianceViolation(models.Model):
    """Compliance violation derived from an EnforcementEvent; tracks GDPR, SOC2, HIPAA, etc."""

    FRAMEWORK_CHOICES = [
        ("GDPR", "GDPR"),
        ("SOC2", "SOC 2"),
        ("HIPAA", "HIPAA"),
        ("ISO27001", "ISO 27001"),
        ("PCIDSS", "PCI DSS"),
        ("CCPA", "CCPA"),
    ]
    STATUS_CHOICES = [("open", "Open"), ("resolved", "Resolved")]
    SEVERITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]

    enforcement_event = models.ForeignKey(
        "EnforcementEvent",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="compliance_violations",
    )
    framework = models.CharField(max_length=16, choices=FRAMEWORK_CHOICES, db_index=True)
    violation_type = models.CharField(max_length=64, blank=True)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default="medium")
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="open", db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.framework} {self.violation_type} ({self.status}) @ {self.created_at}"


class HumanReviewItem(models.Model):
    """Queued item for human review when output guardrails flag content."""

    REVIEW_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    enforcement_event = models.ForeignKey(
        EnforcementEvent,
        on_delete=models.CASCADE,
        related_name="review_items",
    )
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="review_items",
    )
    status = models.CharField(
        max_length=16,
        choices=REVIEW_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_items",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Human Review Item"

    def __str__(self):
        return f"Review({self.status}) event={self.enforcement_event_id}"


class SecurityIncident(models.Model):
    """Formal security incident created from output guard block events."""

    SEVERITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("investigating", "Investigating"),
        ("escalated", "Escalated"),
        ("resolved", "Resolved"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="security_incidents",
    )
    enforcement_event = models.ForeignKey(
        EnforcementEvent,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="incidents",
    )
    title = models.CharField(max_length=512)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default="medium")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="open", db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_incidents",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Security Incident"

    def __str__(self):
        return f"Incident({self.severity}/{self.status}): {self.title[:50]}"


class ComplianceTag(models.Model):
    """Catalog of compliance/regulatory tags for audit annotation.

    DECISION-D Phase 1: seeded from policy.compliance_tags.COMPLIANCE_TAG_METADATA
    via data migration 0030. Admins may add custom tags or override
    label/description/severity; the ``code`` is the stable join key used by
    the gateway when annotating audit events with scan findings.

    Severity scale aligns with MCPToolRegistration.SENSITIVITY_CHOICES.
    """

    SEVERITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]

    code = models.CharField(
        max_length=32,
        primary_key=True,
        help_text="Stable upper-case code (e.g. 'GDPR-PII'). Used as the join key.",
    )
    label = models.CharField(max_length=128)
    regulation = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Citation (e.g. 'EU GDPR Art. 4(1)').",
    )
    description = models.TextField(blank=True, default="")
    severity = models.CharField(
        max_length=16,
        choices=SEVERITY_CHOICES,
        default="medium",
    )
    is_seeded = models.BooleanField(
        default=False,
        help_text="True for tags created by the seed migration; admins may still edit them.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Compliance Tag"
        verbose_name_plural = "Compliance Tags"

    def __str__(self):
        return f"{self.code} ({self.severity})"


from policy.vector_models import VectorCollectionPolicy  # noqa: E402, F401

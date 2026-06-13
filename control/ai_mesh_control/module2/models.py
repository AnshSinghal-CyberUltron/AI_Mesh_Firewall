from django.db import models
from django.utils import timezone


class Playbook(models.Model):
    """Ordered set of automated response steps."""

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="playbooks",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    steps = models.JSONField(
        default=list,
        blank=True,
        help_text='[{action: "kill_switch", params: {...}}, {action: "notify_webhook", ...}]',
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.organization_id})"


class AlertRule(models.Model):
    """Configurable threshold-based alert rule."""

    METRIC_CHOICES = [
        ("block_rate", "Block Rate"),
        ("pii_rate", "PII Rate"),
        ("tier2_score", "Tier-2 Score"),
        ("anomaly_z", "Anomaly Z-Score"),
        ("incident_count", "Incident Count"),
    ]
    OPERATOR_CHOICES = [
        ("gt", "Greater Than"),
        ("lt", "Less Than"),
        ("gte", "Greater Than or Equal"),
        ("lte", "Less Than or Equal"),
    ]
    SEVERITY_CHOICES = [
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("critical", "Critical"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="alert_rules",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    metric = models.CharField(max_length=32, choices=METRIC_CHOICES)
    operator = models.CharField(max_length=8, choices=OPERATOR_CHOICES, default="gt")
    threshold = models.FloatField()
    window_seconds = models.PositiveIntegerField(default=300)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES, default="medium")
    enabled = models.BooleanField(default=True)
    playbook = models.ForeignKey(
        Playbook,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alert_rules",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.metric} {self.operator} {self.threshold})"


class AlertFiring(models.Model):
    """An alert rule that triggered."""

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="firings")
    fired_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    current_value = models.FloatField()
    linked_incident = models.ForeignKey(
        "policy.SecurityIncident",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alert_firings",
    )
    message = models.CharField(max_length=512, blank=True)

    class Meta:
        ordering = ["-fired_at"]

    def __str__(self):
        return f"AlertFiring rule={self.rule_id} @ {self.fired_at}"


class PlaybookRun(models.Model):
    """Execution record for a Playbook."""

    TRIGGER_CHOICES = [
        ("manual", "Manual"),
        ("alert", "Alert"),
        ("anomaly", "Anomaly"),
    ]
    STATUS_CHOICES = [
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
    ]

    playbook = models.ForeignKey(Playbook, on_delete=models.CASCADE, related_name="runs")
    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default="manual")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="running")
    result = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"PlaybookRun {self.playbook_id} ({self.status})"


class AnomalyRule(models.Model):
    """Statistical anomaly detection config per org/agent/model."""

    SCOPE_CHOICES = [
        ("org", "Organization"),
        ("agent", "Agent"),
        ("model", "Model"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="anomaly_rules",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, default="org")
    scope_id = models.CharField(max_length=128, blank=True, default="")
    metric = models.CharField(max_length=64, default="event_rate")
    z_score_threshold = models.FloatField(default=3.0)
    baseline_window_hours = models.PositiveIntegerField(default=168)
    enabled = models.BooleanField(default=True)
    playbook = models.ForeignKey(
        Playbook,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anomaly_rules",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"AnomalyRule {self.scope}:{self.scope_id or '*'} ({self.metric})"


class ThreatIntelEntry(models.Model):
    """External or self-generated threat intelligence entry."""

    SOURCE_CHOICES = [
        ("auto", "Auto"),
        ("manual", "Manual"),
        ("feed", "Feed"),
    ]

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="threat_intel_entries",
    )
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default="manual")
    threat_type = models.CharField(max_length=128)
    indicator = models.TextField(help_text="Regex pattern, IP, or prompt fingerprint")
    owasp_code = models.CharField(max_length=32, blank=True, default="")
    confidence = models.FloatField(default=0.8)
    auto_block = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.threat_type}: {self.indicator[:40]}"

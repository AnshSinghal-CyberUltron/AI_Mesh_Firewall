import hashlib
import logging
import os
import secrets
import uuid
import base64
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from ai_mesh_shared.llm_model_crypto import (
    LLM_MODEL_KEY_ENCRYPTION_ENV,
    build_llm_model_key_cipher,
    decrypt_api_key as shared_decrypt_api_key,
    encrypt_api_key as shared_encrypt_api_key,
)
from cryptography.fernet import InvalidToken

AGENT_TYPE_CHOICES = [
    ("browser", "Browser"),
    ("desktop", "Desktop"),
    ("ide", "IDE"),
    ("mobile", "Mobile"),
    ("api", "API"),
    ("gateway", "Gateway"),
    ("mcp", "MCP"),
    ("agentic", "Agentic"),
]

AGENT_STATUS_CHOICES = [
    ("active", "Active"),
    ("restricted", "Restricted"),
    ("suspended", "Suspended"),
]

KEY_PREFIX_LENGTH = 8
KEY_LENGTH = 48
DEFAULT_RATE_LIMIT_TPM = 100_000
DEFAULT_PERMISSIONS = {
    "allowed_actions": ["chat", "completion", "embedding"],
    "denied_actions": [],
}


def _playground_permissions() -> dict:
    """Permissions for Module 1.6 isolation playground keys (skip threat-intel gate)."""
    perms = DEFAULT_PERMISSIONS.copy()
    perms["playground"] = True
    return perms


def is_isolation_playground_project_id(project_id: str | None) -> bool:
    """True when project_id belongs to Module 1.6 isolation playground keys."""
    return (project_id or "").startswith("isolation-playground-")


def is_live_test_gateway_project_id(project_id: str | None) -> bool:
    """True for org simulator / isolation-playground keys (exempt from key risk telemetry)."""
    pid = project_id or ""
    return pid.startswith("isolation-playground-") or pid.startswith("simulator-")


def _default_permissions() -> dict:
    """Callable default for GatewayAPIKey.permissions (Django requires mutable defaults to be callables)."""
    return DEFAULT_PERMISSIONS.copy()


class SystemConfig(models.Model):
    """Global config snapshot (admin portal, dashboard, SOC, etc.)."""

    version = models.PositiveIntegerField(default=1)
    config = models.JSONField(default=dict)  # dashboard, SOC, threat intel, etc.
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"SystemConfig v{self.version} ({self.updated_at.date()})"


class Endpoint(models.Model):
    """Monitored endpoint."""

    STATUS_CHOICES = [
        ("online", "Online"),
        ("offline", "Offline"),
    ]
    name = models.CharField(max_length=255)
    identifier = models.CharField(max_length=255, help_text="Hostname or unique id")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="offline")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="endpoints",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.identifier})"


class Agent(models.Model):
    """Registered agent (endpoint telemetry or gateway) for SOC and policy attribution."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_type = models.CharField(max_length=32, choices=AGENT_TYPE_CHOICES)
    name = models.CharField(max_length=255)
    endpoint = models.ForeignKey(
        Endpoint,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agents",
    )
    user_id = models.IntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=24,
        choices=AGENT_STATUS_CHOICES,
        default="active",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.get_agent_type_display()})"


class ThreatEvent(models.Model):
    """Threat/block event for SOC and reporting."""

    severity = models.CharField(max_length=32)
    source = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    blocked = models.BooleanField(default=False)
    occurred_at = models.DateTimeField(default=timezone.now)
    endpoint = models.ForeignKey(
        Endpoint,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="threat_events",
    )
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.severity} @ {self.occurred_at}"


class AuditLog(models.Model):
    """Audit trail."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="audit_logs",
        help_text="Owning org for tenant-scoped retention/isolation. Null = system/orphan.",
    )
    action = models.CharField(max_length=128)
    resource = models.CharField(max_length=255, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} on {self.resource or '—'} ({self.created_at})"


class OrganizationAgentKey(models.Model):
    """
    Per-organization API key for agent registration. When an agent registers with this key,
    the backend associates the endpoint/agent with that organization.
    """

    KEY_PREFIX_LENGTH = 8
    KEY_LENGTH = 48

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="agent_keys",
    )
    prefix = models.CharField(
        max_length=KEY_PREFIX_LENGTH,
        editable=False,
        db_index=True,
        help_text="First 8 characters of the plaintext key (for identification in logs).",
    )
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        db_index=True,
        help_text="SHA-256 hex digest of the full API key.",
    )
    name = models.CharField(max_length=128, default="Default registration key")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Organization Agent Key"
        verbose_name_plural = "Organization Agent Keys"

    def __str__(self):
        return f"{self.prefix}... ({self.name}) [{self.organization.slug}]"

    @classmethod
    def hash_raw_key(cls, raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def generate_key(cls, organization, name: str = "Default registration key") -> tuple["OrganizationAgentKey", str]:
        """
        Create a new OrganizationAgentKey. Returns (instance, plaintext_key).
        The plaintext key is shown exactly once (e.g. in download config).
        """
        raw_key = secrets.token_urlsafe(cls.KEY_LENGTH)[: cls.KEY_LENGTH]
        key_hash = cls.hash_raw_key(raw_key)
        prefix = raw_key[: cls.KEY_PREFIX_LENGTH]
        instance = cls.objects.create(
            organization=organization,
            prefix=prefix,
            key_hash=key_hash,
            name=name,
        )
        return instance, raw_key


class OrganizationUpstreamCa(models.Model):
    """
    Corporate root CA bytes for an organization, used only when building
    org-scoped Windows MSI (upstream TLS trust for mitmproxy). One row per org.
    """

    organization = models.OneToOneField(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="upstream_ca",
    )
    cert_bytes = models.BinaryField()
    sha256 = models.CharField(max_length=64, db_index=True)
    size_bytes = models.PositiveIntegerField()
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_org_upstream_cas",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Organization upstream CA"
        verbose_name_plural = "Organization upstream CAs"

    def __str__(self) -> str:
        return f"Upstream CA for {self.organization.slug} ({self.sha256[:12]}…)"


class GatewayAPIKey(models.Model):
    """
    API key for Gateway authentication and context injection.

    Links a hashed API key to a project/tenant with RBAC permissions,
    model allowlists, and rate-limit configuration. The raw key is never
    stored; only the SHA-256 hash is persisted.

    Redis Sync: On save/delete, a post_save/post_delete signal pushes
    the identity payload to 'auth:apikey:{key_hash}' for zero-latency
    Gateway lookups.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gateway_api_keys",
    )
    prefix = models.CharField(
        max_length=KEY_PREFIX_LENGTH,
        editable=False,
        db_index=True,
        help_text="First 8 characters of the plaintext key (for identification in logs).",
    )
    key_hash = models.CharField(
        max_length=64, unique=True, editable=False, db_index=True, help_text="SHA-256 hex digest of the full API key."
    )
    name = models.CharField(max_length=128, help_text="Human-readable label (e.g. 'prod-rag-service').")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gateway_api_keys",
        help_text="User who created / is responsible for this API key.",
    )
    project_id = models.CharField(
        max_length=128, db_index=True, help_text="Associated project or tenant identifier for RBAC and billing."
    )
    permissions = models.JSONField(
        default=_default_permissions,
        blank=True,
        help_text="RBAC Payload (e.g. {'allowed_actions': ['chat', 'embedding'], 'denied_actions': ['fine-tuning']}).",
    )
    allowed_models = models.JSONField(
        default=list,
        blank=True,
        help_text="Model allowlist (e.g. ['gpt-4', 'gpt-3.5-turbo']). Empty means no restrictions.",
    )
    rate_limit_tokens_per_minute = models.PositiveIntegerField(
        default=DEFAULT_RATE_LIMIT_TPM, help_text="Rate limit in Tokens Per Minute (TPM)."
    )
    KEY_PURPOSE_CHOICES = [
        ("production", "Production"),
        ("test", "Test"),
        ("simulator", "Simulator"),
        ("scanner", "Scanner"),
    ]
    UEBA_MODE_CHOICES = [
        ("learning", "Learning"),
        ("active", "Active"),
    ]

    # UNIT (M-25a): FRACTION in [0.0, 1.0]. This is NOT the same scale as
    # ModelState.risk_score, which is a PERCENTAGE in [0.0, 100.0]. Never
    # compare or assign one to the other without an explicit conversion:
    #   percent  = gateway_api_key.risk_score * 100.0
    #   fraction = model_state.risk_score / 100.0
    # Known converting boundaries: core/tasks.py _build_enforcement_metadata
    # (fraction -> 0-100 int security_risk_score) and the gateway service
    # (ai_mesh_gateway/main.py divides ModelState verdict scores by 100.0
    # before mixing them with fraction-scale telemetry risk).
    risk_score = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Unified UEBA final risk score (0.0 = trusted, 1.0 = highest risk). Written by scoring engine.",
    )
    key_purpose = models.CharField(
        max_length=16,
        choices=KEY_PURPOSE_CHOICES,
        default="production",
        db_index=True,
    )
    ueba_mode = models.CharField(
        max_length=16,
        choices=UEBA_MODE_CHOICES,
        default="learning",
        db_index=True,
    )
    ueba_graduation_requests = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Override org default minimum requests before active mode.",
    )
    ueba_graduation_days = models.FloatField(
        null=True,
        blank=True,
        help_text="Override org default minimum days before active mode.",
    )
    ueba_lifetime_request_count = models.PositiveIntegerField(default=0)
    ueba_baseline_locked_at = models.DateTimeField(null=True, blank=True)
    max_context_tokens = models.PositiveIntegerField(
        default=0,
        help_text="Max context tokens per request. 0 = unlimited.",
    )
    mcp_allowed_tools = models.JSONField(
        default=list,
        blank=True,
        help_text="MCP tool allowlist. Empty = all tools allowed.",
    )
    mcp_max_tool_calls = models.PositiveIntegerField(
        default=0,
        help_text="Max MCP tool calls per turn. 0 = unlimited.",
    )
    is_active = models.BooleanField(default=True, db_index=True, help_text="Soft-disable without deletion.")
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional expiry timestamp. Gateway rejects expired keys."
    )
    last_used_at = models.DateTimeField(
        null=True, blank=True, help_text="Updated asynchronously on each Gateway validation."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Fernet-encrypted plaintext, set ONLY for platform-managed shared keys
    # (the per-org "simulator" key) so they can be recovered and applied to
    # every simulator in the org without re-minting. User/prod keys stay
    # hash-only (this field stays empty for them).
    encrypted_secret = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Gateway API Key"
        verbose_name_plural = "Gateway API Keys"

    def __str__(self) -> str:
        return f"{self.prefix}... ({self.name}) [{'active' if self.is_active else 'disabled'}]"

    @classmethod
    def hash_raw_key(cls, raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def clean(self) -> None:
        super().clean()
        if not (0.0 <= self.risk_score <= 1.0):
            raise ValidationError({"risk_score": "Risk score must be between 0.0 and 1.0."})

    @classmethod
    def generate_key(
        cls,
        name: str,
        owner,
        project_id: str,
        permissions: dict | None = None,
        allowed_models: list | None = None,
        rate_limit_tokens_per_minute: int | None = DEFAULT_RATE_LIMIT_TPM,
        risk_score: float | None = 0.0,
        expires_at=None,
        max_context_tokens: int = 0,
        mcp_allowed_tools: list | None = None,
        mcp_max_tool_calls: int = 0,
    ) -> tuple["GatewayAPIKey", str]:
        """
        Create a new GatewayAPIKey with a cryptographically secure key.
        Returns a tuple of (instance, plaintext_key). The plaintext key is shown exactly once and is never stored.
        """
        raw_key = secrets.token_urlsafe(KEY_LENGTH)[:KEY_LENGTH]
        key_hash = cls.hash_raw_key(raw_key)
        prefix = raw_key[:KEY_PREFIX_LENGTH]

        instance = cls.objects.create(
            prefix=prefix,
            key_hash=key_hash,
            name=name,
            owner=owner,
            project_id=project_id,
            permissions=permissions if permissions is not None else DEFAULT_PERMISSIONS.copy(),
            allowed_models=allowed_models if allowed_models is not None else [],
            rate_limit_tokens_per_minute=rate_limit_tokens_per_minute,
            risk_score=risk_score,
            expires_at=expires_at,
            max_context_tokens=max_context_tokens,
            mcp_allowed_tools=mcp_allowed_tools if mcp_allowed_tools is not None else [],
            mcp_max_tool_calls=mcp_max_tool_calls,
        )
        return instance, raw_key

    @classmethod
    def ensure_default_for_org(cls, organization, owner) -> tuple["GatewayAPIKey", str | None]:
        """Return the default MCP gateway key for *organization*, creating one if needed.

        Returns ``(instance, raw_key)`` where *raw_key* is the plaintext key
        when a new key was created, or ``None`` when an existing key was found.
        The plaintext is only available at creation time.

        Concurrency: the whole check-then-create section runs inside
        ``transaction.atomic()`` with a ``select_for_update`` on the organization
        row (mirroring ``ensure_isolation_playground_for_org``). Locking the org
        row serializes concurrent provisioning per org so two callers that both
        observe "no active key" cannot each mint a duplicate active key.
        """
        project_id = f"mcp-default-{organization.slug}"
        with transaction.atomic():
            type(organization).objects.select_for_update().get(pk=organization.pk)
            existing = (
                cls.objects.select_for_update()
                .filter(
                    organization=organization,
                    project_id=project_id,
                    is_active=True,
                )
                .first()
            )
            if existing:
                # Idempotently re-push the existing key to Redis: a Redis flush /
                # container recycle may have evicted it, in which case handing the
                # caller a key the gateway 401s on is useless. save() re-fires the
                # post_save -> Redis sync (no field value change).
                try:
                    existing.save(update_fields=["is_active"])
                except Exception:  # noqa: BLE001 - never fail provisioning on a resync hiccup
                    pass
                return existing, None

            instance, raw_key = cls.generate_key(
                name=f"MCP Default Key ({organization.name})",
                owner=owner,
                project_id=project_id,
            )
            # organization is auto-set via save(), but be explicit
            if not instance.organization_id:
                instance.organization = organization
                instance.save(update_fields=["organization"])
            return instance, raw_key

    def store_secret(self, raw_key: str) -> None:
        """Persist the Fernet-encrypted plaintext so this key can be recovered.
        Used ONLY for platform-managed shared keys (the org simulator key)."""
        if not raw_key:
            return
        cipher = _build_llm_model_key_cipher()
        self.encrypted_secret = cipher.encrypt(raw_key.encode("utf-8")).decode("utf-8")
        self.save(update_fields=["encrypted_secret"])

    def recover_secret(self) -> str | None:
        """Decrypt the stored plaintext, or None if absent/undecryptable."""
        if not self.encrypted_secret:
            return None
        cipher = _build_llm_model_key_cipher()
        try:
            return cipher.decrypt(self.encrypted_secret.encode("utf-8")).decode("utf-8")
        except Exception:
            return None

    @classmethod
    def ensure_simulator_for_org(cls, organization, owner) -> tuple["GatewayAPIKey", str | None]:
        """Return the org's simulator gateway key, creating one if needed.

        Uses a stable ``project_id`` of ``simulator-{slug}`` so Module 1 simulators
        can auto-provision without manual key entry. For recoverable keys the same
        plaintext is returned on EVERY call (one stable per-org key); plaintext is
        withheld only for legacy non-recoverable keys that are not being re-minted.
        """
        project_id = f"simulator-{organization.slug}"
        # Concurrency: serialize provisioning per org by locking the org row
        # (mirrors ensure_isolation_playground_for_org). Locking only the key
        # rows cannot prevent the duplicate-create race when NO key exists yet,
        # so the org row is locked first — a second caller blocks until the
        # first commits, then sees the freshly minted key instead of minting a
        # duplicate active simulator key.
        with transaction.atomic():
            type(organization).objects.select_for_update().get(pk=organization.pk)
            # Self-healing single-key invariant: collect ALL active simulator keys
            # for the org (canonical project_id OR legacy name="simulator"), keep
            # exactly ONE — the most-recently-used canonical key (so the key that
            # browsers/scripts are actively using survives) — and deactivate the
            # rest. Historically the ensure/rotate path churned the org through
            # dozens of keys and left multiple active; this collapses them to one
            # WITHOUT minting anything.
            active = list(
                cls.objects.select_for_update()
                .filter(organization=organization, is_active=True)
                .filter(
                    models.Q(project_id=project_id)
                    | models.Q(name="simulator")
                    | models.Q(name__startswith="simulator-")
                    | models.Q(project_id__startswith="simulator-")
                )
            )
            if active:
                def _recency(k):
                    return k.last_used_at or k.created_at
                canonical = max(
                    (k for k in active if k.project_id == project_id),
                    default=None, key=_recency,
                ) or max(active, key=_recency)
                recovered = canonical.recover_secret()
                if recovered is not None:
                    # Recoverable: reuse the SAME key for every simulator in the
                    # org (no re-mint, no churn); just collapse any duplicates.
                    for k in active:
                        if k.pk != canonical.pk:
                            k.is_active = False
                            k.save(update_fields=["is_active"])
                    return canonical, recovered
                # Legacy key(s) minted before recoverable storage existed:
                # deactivate ALL and mint ONE fresh recoverable key (a one-time
                # migration per org; afterwards every provision returns the same
                # recoverable key).
                for k in active:
                    k.is_active = False
                    k.save(update_fields=["is_active"])

            instance, raw_key = cls.generate_key(
                name="simulator",
                owner=owner,
                project_id=project_id,
                allowed_models=[],
            )
            if not instance.organization_id:
                instance.organization = organization
                instance.save(update_fields=["organization"])
            instance.store_secret(raw_key)  # persist encrypted so it stays recoverable
            return instance, raw_key

    @classmethod
    def rotate_simulator_for_org(cls, organization, owner) -> tuple["GatewayAPIKey", str]:
        """Deactivate the org's existing simulator key(s) and issue a fresh one.

        Plaintext keys are hash-only (never recoverable), so when a browser needs a
        usable simulator credential but the existing key's plaintext is gone (e.g.
        cleared localStorage, new device), rotation is the only way to hand back a
        working key. Always returns plaintext. Deactivation uses save() so the
        post_save signal propagates is_active=False to Redis (the gateway then
        rejects the stale key); generate_key syncs the new key the same way.
        """
        project_id = f"simulator-{organization.slug}"
        stale = list(
            cls.objects.filter(organization=organization, is_active=True).filter(
                models.Q(project_id=project_id) | models.Q(name="simulator")
            )
        )
        for key in stale:
            key.is_active = False
            key.save(update_fields=["is_active"])

        instance, raw_key = cls.generate_key(
            name="simulator",
            owner=owner,
            project_id=project_id,
            allowed_models=[],
        )
        if not instance.organization_id:
            instance.organization = organization
            instance.save(update_fields=["organization"])
        return instance, raw_key

    @classmethod
    def promote_as_org_simulator(
        cls, organization, key: "GatewayAPIKey"
    ) -> tuple["GatewayAPIKey", str | None]:
        """Bind an existing key as the org's single active simulator credential."""
        if key.organization_id != organization.pk:
            raise ValueError("Key does not belong to organization")
        project_id = f"simulator-{organization.slug}"
        with transaction.atomic():
            type(organization).objects.select_for_update().get(pk=organization.pk)
            simulator_filter = (
                models.Q(project_id=project_id)
                | models.Q(name="simulator")
                | models.Q(name__startswith="simulator-")
                | models.Q(project_id__startswith="simulator-")
            )
            for other in (
                cls.objects.select_for_update()
                .filter(organization=organization, is_active=True)
                .filter(simulator_filter)
                .exclude(pk=key.pk)
            ):
                other.is_active = False
                other.save(update_fields=["is_active"])
            key.project_id = project_id
            if not str(key.name or "").strip():
                key.name = "simulator"
            key.is_active = True
            key.save(update_fields=["project_id", "name", "is_active"])
            return key, key.recover_secret()

    @classmethod
    def ensure_isolation_playground_for_org(
        cls, organization, owner
    ) -> tuple["GatewayAPIKey", str | None]:
        """Return the org's isolation playground key, creating one if needed.

        Uses ``project_id=isolation-playground-{slug}`` so Module 1.6 live tests
        do not share risk_score with the attack simulator key.

        M-25b: the whole get-or-create critical section runs inside
        ``transaction.atomic()`` with ``select_for_update``. Locking only the
        key rows cannot prevent the duplicate-create race when NO key exists
        yet (there is nothing to lock), so the organization row is locked
        first — serializing concurrent provisioning per org. A second caller
        blocks on the org lock until the first commits, then sees (and
        returns) the freshly created key instead of minting a duplicate.
        """
        project_id = f"isolation-playground-{organization.slug}"
        with transaction.atomic():
            type(organization).objects.select_for_update().get(pk=organization.pk)
            active = list(
                cls.objects.select_for_update()
                .filter(organization=organization, is_active=True)
                .filter(
                    models.Q(project_id=project_id) | models.Q(name="isolation-playground")
                )
            )
            if active:
                def _recency(k):
                    return k.last_used_at or k.created_at

                canonical = max(
                    (k for k in active if k.project_id == project_id),
                    default=None,
                    key=_recency,
                ) or max(active, key=_recency)
                recovered = canonical.recover_secret()
                if recovered is not None:
                    updates = []
                    if not canonical.permissions.get("playground"):
                        canonical.permissions = {**canonical.permissions, "playground": True}
                        updates.append("permissions")
                    for k in active:
                        if k.pk != canonical.pk:
                            k.is_active = False
                            k.save(update_fields=["is_active"])
                    if updates:
                        canonical.save(update_fields=updates)
                    return canonical, recovered
                for k in active:
                    k.is_active = False
                    k.save(update_fields=["is_active"])

            instance, raw_key = cls.generate_key(
                name="isolation-playground",
                owner=owner,
                project_id=project_id,
                permissions=_playground_permissions(),
                allowed_models=[],
                risk_score=0.0,
            )
            if not instance.organization_id:
                instance.organization = organization
                instance.save(update_fields=["organization"])
            instance.store_secret(raw_key)
        return instance, raw_key

    @classmethod
    def rotate_isolation_playground_for_org(
        cls, organization, owner
    ) -> tuple["GatewayAPIKey", str]:
        """Deactivate existing isolation playground keys and mint a fresh one."""
        project_id = f"isolation-playground-{organization.slug}"
        stale = list(
            cls.objects.filter(organization=organization, is_active=True).filter(
                models.Q(project_id=project_id) | models.Q(name="isolation-playground")
            )
        )
        for key in stale:
            key.is_active = False
            key.save(update_fields=["is_active"])

        instance, raw_key = cls.generate_key(
            name="isolation-playground",
            owner=owner,
            project_id=project_id,
            permissions=_playground_permissions(),
            allowed_models=[],
            risk_score=0.0,
        )
        if not instance.organization_id:
            instance.organization = organization
            instance.save(update_fields=["organization"])
        instance.store_secret(raw_key)
        return instance, raw_key

    def save(self, *args, **kwargs):
        if not self.organization_id and self.owner_id:
            try:
                self.organization = self.owner.profile.organization
            except Exception:
                pass
        super().save(*args, **kwargs)

    def build_redis_payload(self) -> dict:
        """
        Build the identity payload that gets cached in Redis.
        This is the ``X-ZeroShield-Context`` that the Gateway injects
        into every authenticated request.
        """
        org = self.organization
        # ── G8: include the owning user's role names so downstream policy
        # evaluation can match against `Policy.allowed_roles`. Roles are
        # RBAC labels (e.g. "analyst", "org_admin"), not secrets — they
        # live next to existing RBAC payload fields (`permissions`,
        # `mcp_allowed_tools`) which are already cached here. We isolate
        # the lookup in try/except + logger.warning so a transient DB
        # error during the M2M traversal can't blank the whole payload
        # (which would deny-by-default; never silent on failure).
        roles: list[str] = []
        try:
            profile = getattr(self.owner, "profile", None)
            if profile is not None:
                roles = list(profile.roles.values_list("name", flat=True))
        except Exception as exc:  # pragma: no cover - defensive
            import logging
            logging.getLogger(__name__).warning(
                "GatewayAPIKey.build_redis_payload roles lookup failed key_id=%s err=%s",
                self.id, exc,
            )
            roles = []
        return {
            "key_id": str(self.id),
            "prefix": self.prefix,
            "user_id": self.owner_id,  # type: ignore[attr-defined]  # Django FK _id accessor
            "project_id": self.project_id,
            "organization_id": org.id if org else None,
            "org_slug": org.slug if org else "",
            "permissions": self.permissions,
            "allowed_models": self.allowed_models,
            "rate_limit_tpm": self.rate_limit_tokens_per_minute,
            "risk_score": self.risk_score,
            "max_context_tokens": self.max_context_tokens,
            "mcp_allowed_tools": self.mcp_allowed_tools,
            "mcp_max_tool_calls": self.mcp_max_tool_calls,
            "roles": roles,
            "is_active": self.is_active,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


KILL_SWITCH_ACTION_CHOICES = [
    ("disable", "Disable"),
    ("reroute", "Reroute"),
]


class KillSwitch(models.Model):
    """
    Emergency kill-switch for individual models or all models globally.

    Synced to Redis for zero-latency enforcement in the Gateway.
    Redis key pattern:
    - ``kill_switch:global`` when model_name == ``__global__``
    - ``kill_switch:model:{model_name}`` for per-model switches

    Actions:
    - ``disable``: immediately reject all requests for the model with 503
    - ``reroute``: redirect requests to ``fallback_model`` transparently
    """

    SCOPE_GLOBAL = "__global__"
    # Credential-wide: block all models for a single API key prefix.
    SCOPE_CREDENTIAL = "__credential__"

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="kill_switches",
    )
    model_name = models.CharField(
        max_length=255,
        help_text="Model name to kill, or '__global__' for all models.",
    )
    api_key_prefix = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Optional API key prefix for credential-scoped kill-switch. Blank = org-wide.",
    )
    is_active = models.BooleanField(default=False, db_index=True)
    action = models.CharField(
        max_length=20,
        choices=KILL_SWITCH_ACTION_CHOICES,
        default="disable",
    )
    fallback_model = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Fallback model for 'reroute' action. Ignored for 'disable'.",
    )
    reason = models.TextField(blank=True, default="")
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kill_switches",
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Kill Switch"
        verbose_name_plural = "Kill Switches"
        unique_together = [("organization", "model_name", "api_key_prefix")]

    def __str__(self) -> str:
        status = "ACTIVE" if self.is_active else "inactive"
        return f"KillSwitch({self.model_name}, {self.action}, {status})"

    def clean(self) -> None:
        super().clean()
        if self.action == "reroute" and not self.fallback_model:
            raise ValidationError({"fallback_model": "Fallback model is required when action is 'reroute'."})
        if self.fallback_model and self.fallback_model == self.model_name:
            raise ValidationError({"fallback_model": "fallback_model must differ from model_name (self-loop)."})

    def _org_prefix(self) -> str:
        return self.organization.slug if self.organization_id else "default"

    def build_redis_key(self) -> str:
        prefix = self._org_prefix()
        if self.model_name == self.SCOPE_GLOBAL:
            return f"kill_switch:{prefix}:global"
        credential_prefix = (self.api_key_prefix or "").strip()
        if credential_prefix and self.model_name == self.SCOPE_CREDENTIAL:
            return f"kill_switch:{prefix}:credential:{credential_prefix}"
        if credential_prefix:
            return f"kill_switch:{prefix}:credential:{credential_prefix}:model:{self.model_name}"
        return f"kill_switch:{prefix}:model:{self.model_name}"

    def build_redis_payload(self) -> dict:
        org_slug = self._org_prefix()
        if self.organization_id and self.organization and self.organization.slug != org_slug:
            import logging

            logging.getLogger(__name__).error(
                "KillSwitch org_slug mismatch: organization.slug=%s payload org_slug=%s id=%s",
                self.organization.slug,
                org_slug,
                self.pk,
            )
            org_slug = self.organization.slug
        return {
            "is_active": self.is_active,
            "action": self.action,
            "fallback_model": self.fallback_model,
            "reason": self.reason,
            "organization_id": self.organization_id,
            "org_slug": org_slug,
            "api_key_prefix": (self.api_key_prefix or "").strip(),
            "activated_at": self.activated_at.isoformat() if self.activated_at else None,
        }


ENFORCEMENT_MODE_CHOICES = [
    ("block", "Block"),
    ("monitor", "Monitor"),
    ("audit", "Audit"),
]

# Per-detector output guardrail actions (§1.7 Generator-Level Output Guardrails).
# Each output detector (PII, credential, IP leakage, policy, hallucination) can be
# independently routed to one of these actions, giving operators full control.
OUTPUT_GUARD_ACTION_CHOICES = [
    ("block", "Block"),
    ("redact", "Redact"),
    ("rewrite", "Rewrite"),
    ("flag", "Flag"),
    ("allow", "Allow"),
]

LOG_LEVEL_CHOICES = [
    ("minimal", "Minimal"),
    ("standard", "Standard"),
    ("detailed", "Detailed"),
    ("verbose", "Verbose"),
]


def _default_compliance_frameworks() -> list:
    return ["SOC2", "ISO27001"]


class FirewallConfig(models.Model):
    """
    Per-organization firewall configuration. Synced to Redis for gateway hot-reload.

    All 30+ settings from the frontend are backed by typed Django fields
    with validators. On save, a ``post_save`` signal pushes the full
    configuration to Redis (``firewall:config:{org_slug}``) and publishes
    to the ``config_updates`` Pub/Sub channel for gateway hot-reload.
    """

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="firewall_configs",
        unique=True,
    )

    # -- General --
    firewall_enabled = models.BooleanField(
        default=True,
        help_text="Master switch for all firewall enforcement.",
    )
    enforcement_mode = models.CharField(
        max_length=16,
        choices=ENFORCEMENT_MODE_CHOICES,
        default="block",
        help_text="How the firewall handles policy violations.",
    )
    log_level = models.CharField(
        max_length=16,
        choices=LOG_LEVEL_CHOICES,
        default="detailed",
        help_text="Detail level for security logs.",
    )

    # -- Rate Limiting --
    rate_limit_enabled = models.BooleanField(
        default=True,
        help_text="Protect against abuse and DDoS.",
    )
    requests_per_minute = models.PositiveIntegerField(
        default=1000,
        validators=[MaxValueValidator(10000)],
        help_text="Maximum requests allowed per minute (global).",
    )
    burst_limit = models.PositiveIntegerField(
        default=150,
        validators=[MaxValueValidator(1000)],
        help_text="Maximum requests in a short burst.",
    )
    # 1.1c: org-wide tokens-per-minute ceiling. The gateway already enforces this
    # fail-closed (RATE_LIMITER.check_org_rate_limit) but the control plane never
    # emitted the key, so the ceiling was always 0 (disabled / multi-key stacking
    # defeated per-key TPM). 0 keeps it disabled (backward-safe); any positive
    # value activates the existing gateway Lua check across ALL of an org's keys.
    org_tpm_limit = models.PositiveIntegerField(
        default=0,
        help_text="Org-wide tokens-per-minute ceiling across all keys (0 = disabled).",
    )

    # -- Content Filtering --
    content_filtering_enabled = models.BooleanField(
        default=True,
        help_text="Scan and filter sensitive content.",
    )
    pii_detection_enabled = models.BooleanField(
        default=True,
        help_text="Detect and redact personally identifiable information.",
    )
    toxicity_threshold = models.FloatField(
        default=0.70,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Minimum score to flag toxic content (0-1).",
    )
    blocked_keywords = models.TextField(
        default="password, secret, api_key, token",
        blank=True,
        help_text="Comma-separated list of keywords to block.",
    )

    # -- Model Governance --
    model_isolation_enabled = models.BooleanField(
        default=True,
        help_text="Enforce strict model boundaries.",
    )
    allowed_models = models.TextField(
        default="",
        blank=True,
        help_text="Comma-separated list of approved models.",
    )
    default_model = models.CharField(
        max_length=64,
        default="",
        blank=True,
        help_text="Fallback model when none specified.",
    )

    # -- Prompt Security --
    jailbreak_detection_enabled = models.BooleanField(
        default=True,
        help_text="Detect and block jailbreak attempts.",
    )
    semantic_analysis_enabled = models.BooleanField(
        default=True,
        help_text="Deep analysis of prompt intent (Tier-2 ML scanning).",
    )
    tier2_fail_closed_enabled = models.BooleanField(
        default=True,
        help_text="If Tier-2 scanning degrades (timeout/parse issues), fail closed in block mode.",
    )
    tier2_execution_mode = models.CharField(
        max_length=32,
        choices=[
            ("sync_pre_llm", "Sync Pre-LLM"),
            ("async_post_llm", "Async Post-LLM"),
        ],
        default="sync_pre_llm",
        help_text="Tier-2 execution strategy for Bedrock scanning.",
    )
    tier2_stream_hold_enabled = models.BooleanField(
        default=False,
        help_text="Allow stream-hold mode before first token when async Tier-2 is configured.",
    )
    tier2_stream_hold_timeout_ms = models.PositiveIntegerField(
        default=1200,
        validators=[MinValueValidator(500), MaxValueValidator(2000)],
        help_text="Max pre-stream hold duration in milliseconds for Tier-2 block mode.",
    )
    # Phase 0 D-G2-v3: per-org Tier-2 override + strict mode.
    # tier2_enabled is a tri-state override over the gateway default:
    #   None  -> inherit gateway-wide TIER2_ENABLED (no per-org opinion)
    #   True  -> force-enable Tier-2 for this org
    #   False -> force-disable Tier-2 for this org
    # The gateway MUST use an "is None" identity check (not truthiness)
    # to distinguish "no override" from "explicit False".
    tier2_enabled = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Per-org override for Tier-2 (Bedrock LLM scan). "
            "None = inherit gateway default; True/False = explicit override."
        ),
    )
    # MCP-specific Tier-2 gate (separate from chat ``tier2_enabled``).
    mcp_tier2_enabled = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Per-org override for MCP Tier-2 (Bedrock) after Tier-1 allows. "
            "None = inherit gateway default; True/False = explicit override."
        ),
    )
    # Transparent external MCP proxy posture. That surface is transport-level:
    # the gateway knows the org but there is no per-server scan-control row to
    # select an action on, so the operator picks the posture here. Default is
    # "tag" (observe-only) because we never enforce anything the operator did
    # not explicitly choose.
    mcp_ext_scan_action = models.CharField(
        max_length=16,
        choices=[
            ("tag", "Tag only (observe, never mutate)"),
            ("redact", "Redact"),
            ("block", "Block"),
        ],
        default="tag",
        help_text=(
            "Action applied to traffic through the transparent external MCP "
            "proxy (/v1/mcp/ext-proxy/<host>). 'tag' ENFORCES NOTHING: findings "
            "are detected, tagged and emitted, but the payload is never mutated "
            "and the call is never blocked. Choose 'redact' or 'block' to "
            "actually enforce on this surface."
        ),
    )
    # tier2_strict controls behavior when Tier-2 is unavailable (circuit
    # breaker OPEN, Bedrock degraded, etc.). Per Security Hawk F5 override,
    # default is True: refuse the request with HTTP 451 reason_code
    # "tier2_unavailable_strict" rather than silently passing through
    # Tier-1-only. Operators can opt into degraded-pass per-org by setting
    # this to False; gateway emits a tier2_degraded_pass event in that case.
    tier2_strict = models.BooleanField(
        default=True,
        help_text=(
            "When Tier-2 is configured but unavailable, refuse the request "
            "(HTTP 451) rather than passing through Tier-1-only. "
            "Default True per Phase 0 D-G3-v3."
        ),
    )
    prompt_injection_threshold = models.FloatField(
        default=0.80,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Sensitivity for injection detection (0-1).",
    )

    # -- Response Guardrails --
    response_filtering_enabled = models.BooleanField(
        default=True,
        help_text="Scan and filter model responses.",
    )
    factuality_check_enabled = models.BooleanField(
        default=False,
        help_text="Verify response accuracy (hallucination check).",
    )
    # Phase 1 D_G10: per-org semantic-grounding configuration.
    # Mode "lexical" preserves legacy Jaccard behaviour; "semantic" uses
    # Bedrock Titan v2 embeddings; "hybrid" averages the two scores.
    # Default lexical so a config rollout cannot change scoring before the
    # operator opts in. See `runs/verification/D_G10.md` §1.6.
    hallucination_grounding_mode = models.CharField(
        max_length=16,
        choices=[
            ("lexical", "Lexical (Jaccard)"),
            ("semantic", "Semantic (Bedrock Titan v2)"),
            ("hybrid", "Hybrid (avg lex+sem)"),
        ],
        default="lexical",
        help_text=(
            "Hallucination grounding scoring mode. 'lexical' = legacy "
            "Jaccard; 'semantic' = Bedrock Titan v2; 'hybrid' = average."
        ),
    )
    hallucination_grounding_threshold = models.FloatField(
        default=0.2,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text=(
            "Minimum risk_score that flags a response as hallucinated. "
            "Default 0.2 preserves the legacy guard threshold."
        ),
    )
    hallucination_grounding_model = models.CharField(
        max_length=128,
        default="",
        blank=True,
        help_text=(
            "Override embedding model id. Empty = use gateway default "
            "(env BEDROCK_MODEL, typically 'amazon.titan-embed-text-v2:0')."
        ),
    )

    # -- §1.7 Generator-Level Output Guardrails: per-detector control --
    # Master switch is `response_filtering_enabled` (output_scan_enabled). Each
    # detector below has an independent enable toggle and an action selector so
    # operators have full control over the output path. The hallucination detector
    # reuses `factuality_check_enabled` (enable) and `hallucination_grounding_threshold`.
    output_pii_enabled = models.BooleanField(
        default=True,
        help_text="Detect PII / personal-data leakage in model responses.",
    )
    output_pii_action = models.CharField(
        max_length=16,
        choices=OUTPUT_GUARD_ACTION_CHOICES,
        default="redact",
        help_text="Action applied when PII is detected in a response.",
    )
    output_credential_enabled = models.BooleanField(
        default=True,
        help_text="Detect credential / secret exposure in model responses.",
    )
    output_credential_action = models.CharField(
        max_length=16,
        choices=OUTPUT_GUARD_ACTION_CHOICES,
        default="block",
        help_text="Action applied when credentials/secrets are detected.",
    )
    output_ip_leakage_enabled = models.BooleanField(
        default=True,
        help_text="Detect intellectual-property / infrastructure leakage in responses.",
    )
    output_ip_leakage_action = models.CharField(
        max_length=16,
        choices=OUTPUT_GUARD_ACTION_CHOICES,
        default="flag",
        help_text="Action applied when IP/infrastructure leakage is detected.",
    )
    output_policy_enabled = models.BooleanField(
        default=True,
        help_text="Enforce policy-engine verdicts on the output (post-LLM) path.",
    )
    output_policy_action = models.CharField(
        max_length=16,
        choices=OUTPUT_GUARD_ACTION_CHOICES,
        default="block",
        help_text="Action applied when an output policy violation is detected.",
    )
    output_hallucination_action = models.CharField(
        max_length=16,
        choices=OUTPUT_GUARD_ACTION_CHOICES,
        default="flag",
        help_text=(
            "Action applied when hallucination risk is detected. Enable toggle is "
            "`factuality_check_enabled`; threshold is `hallucination_grounding_threshold`."
        ),
    )
    output_incident_logging_enabled = models.BooleanField(
        default=True,
        help_text="Log security incidents (telemetry + audit) for output-guard actions.",
    )

    max_response_tokens = models.PositiveIntegerField(
        default=4096,
        validators=[MaxValueValidator(32000)],
        help_text="Maximum tokens in model response.",
    )

    # -- RAG Security --
    rag_enabled = models.BooleanField(
        default=True,
        help_text="Enable Retrieval-Augmented Generation.",
    )
    vector_db_isolation = models.BooleanField(
        default=True,
        help_text="Enforce tenant isolation in vector databases.",
    )
    rag_max_documents = models.PositiveIntegerField(
        default=5,
        validators=[MaxValueValidator(20)],
        help_text="Maximum documents to retrieve.",
    )
    rag_relevance_threshold = models.FloatField(
        default=0.75,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Minimum similarity score for retrieval (0-1).",
    )
    rag_redaction_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Redact sensitive values with vector-safe typed placeholders "
            "([EMAIL], [SSN], [CREDIT_CARD], ...) before embedding RAG "
            "documents at ingestion. Preserves semantics for vector matching."
        ),
    )
    rag_tier2_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Run the ML Tier-2 guard model (boto3 Bedrock) on RAG ingestion "
            "documents and vector queries, in addition to static Tier-1 scanning."
        ),
    )

    # -- Threat Intelligence --
    threat_intel_enabled = models.BooleanField(
        default=True,
        help_text="Integrate with threat intelligence feeds.",
    )
    auto_block_threats = models.BooleanField(
        default=True,
        help_text="Automatically block known threat actors.",
    )
    threat_score_threshold = models.PositiveIntegerField(
        default=75,
        validators=[MaxValueValidator(100)],
        help_text="Minimum score to trigger blocking (0-100).",
    )

    # -- Audit & Compliance --
    audit_logging_enabled = models.BooleanField(
        default=True,
        help_text="Comprehensive audit trail for compliance.",
    )
    retention_days = models.PositiveIntegerField(
        default=90,
        validators=[MaxValueValidator(365)],
        help_text="How long to retain audit logs (days).",
    )
    compliance_frameworks = models.JSONField(
        default=_default_compliance_frameworks,
        blank=True,
        help_text="Active compliance frameworks (e.g. ['SOC2', 'ISO27001']).",
    )

    # -- Routing Governance --
    routing_risk_weight = models.FloatField(
        default=0.30,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Weight for risk dimension in model routing (0-1).",
    )
    routing_cost_weight = models.FloatField(
        default=0.20,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Weight for cost dimension in model routing (0-1).",
    )
    routing_latency_weight = models.FloatField(
        default=0.20,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Weight for latency dimension in model routing (0-1).",
    )
    routing_priority_weight = models.FloatField(
        default=0.30,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Weight for priority dimension in model routing (0-1).",
    )
    default_data_sensitivity = models.CharField(
        max_length=16,
        choices=[
            ("public", "Public"),
            ("internal", "Internal"),
            ("confidential", "Confidential"),
            ("restricted", "Restricted"),
        ],
        default="internal",
        help_text="Default data sensitivity level for routing decisions.",
    )
    routing_enabled = models.BooleanField(
        default=True,
        help_text="Enable Bedrock-adjudicated dynamic model routing for /v1/chat/completions.",
    )

    # -- Meta --
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="firewall_config_updates",
    )

    class Meta:
        verbose_name = "Firewall Configuration"
        verbose_name_plural = "Firewall Configuration"

    def __str__(self) -> str:
        mode = self.enforcement_mode
        state = "enabled" if self.firewall_enabled else "disabled"
        return f"FirewallConfig (mode={mode}, {state})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    @classmethod
    def load(cls, organization=None) -> "FirewallConfig":
        if organization is not None:
            obj, _ = cls.objects.get_or_create(organization=organization)
        else:
            obj, _ = cls.objects.get_or_create(organization__isnull=True)
        return obj

    def build_redis_key(self) -> str:
        slug = self.organization.slug if self.organization_id else "default"
        return f"firewall:config:{slug}"

    def build_gateway_payload(self) -> dict:
        """
        Build the configuration dict for Redis/Gateway consumption.

        Maps model field names to gateway CONFIG keys where they differ.
        """
        blocked_kw = (
            [kw.strip() for kw in self.blocked_keywords.split(",") if kw.strip()] if self.blocked_keywords else []
        )

        allowed_mdl = [m.strip() for m in self.allowed_models.split(",") if m.strip()] if self.allowed_models else []

        return {
            "firewall_enabled": self.firewall_enabled,
            "enforcement_mode": self.enforcement_mode,
            "log_level": self.log_level,
            "rate_limit_enabled": self.rate_limit_enabled,
            "requests_per_minute": self.requests_per_minute,
            "burst_limit": self.burst_limit,
            "org_tpm_limit": self.org_tpm_limit,  # 1.1c: gateway consumes this for the org-wide TPM ceiling
            "input_scan_enabled": self.content_filtering_enabled,
            "scan_block_on_pii": self.pii_detection_enabled,
            "toxicity_threshold": self.toxicity_threshold,
            "blocked_keywords": blocked_kw,
            "model_isolation_enabled": self.model_isolation_enabled,
            "allowed_models": allowed_mdl,
            "litellm_default_model": self.default_model,
            "scan_block_on_injection": self.jailbreak_detection_enabled,
            "deep_scan_enabled": self.semantic_analysis_enabled,
            "tier2_fail_closed_enabled": self.tier2_fail_closed_enabled,
            "tier2_execution_mode": self.tier2_execution_mode,
            "tier2_stream_hold_enabled": self.tier2_stream_hold_enabled,
            "tier2_stream_hold_timeout_ms": self.tier2_stream_hold_timeout_ms,
            # Phase 0 D-G2-v3 / D-G3-v3: per-org Tier-2 override + strict mode.
            # tier2_enabled is tri-state — preserve None so gateway can
            # distinguish "no per-org opinion" from "explicit False".
            "tier2_enabled": self.tier2_enabled,
            "mcp_tier2_enabled": self.mcp_tier2_enabled,
            "mcp_ext_scan_action": self.mcp_ext_scan_action,
            "tier2_strict": self.tier2_strict,
            "prompt_injection_threshold": self.prompt_injection_threshold,
            "output_scan_enabled": self.response_filtering_enabled,
            "hallucination_flag_enabled": self.factuality_check_enabled,
            # Phase 1 D_G10: gateway reads these to choose lex / sem / hybrid.
            "hallucination_grounding_mode": self.hallucination_grounding_mode,
            "hallucination_grounding_threshold": self.hallucination_grounding_threshold,
            "hallucination_grounding_model": self.hallucination_grounding_model,
            # §1.7 per-detector output guardrail controls.
            "output_pii_enabled": self.output_pii_enabled,
            "output_pii_action": self.output_pii_action,
            "output_credential_enabled": self.output_credential_enabled,
            "output_credential_action": self.output_credential_action,
            "output_ip_leakage_enabled": self.output_ip_leakage_enabled,
            "output_ip_leakage_action": self.output_ip_leakage_action,
            "output_policy_enabled": self.output_policy_enabled,
            "output_policy_action": self.output_policy_action,
            "output_hallucination_action": self.output_hallucination_action,
            "output_incident_logging_enabled": self.output_incident_logging_enabled,
            "max_response_tokens": self.max_response_tokens,
            "rag_enabled": self.rag_enabled,
            "vector_db_isolation": self.vector_db_isolation,
            "rag_default_max_results": self.rag_max_documents,
            "rag_relevance_threshold": self.rag_relevance_threshold,
            "rag_redaction_enabled": self.rag_redaction_enabled,
            "rag_tier2_enabled": self.rag_tier2_enabled,
            "threat_intel_enabled": self.threat_intel_enabled,
            "auto_block_threats": self.auto_block_threats,
            "threat_score_threshold": self.threat_score_threshold,
            "telemetry_enabled": self.audit_logging_enabled,
            "retention_days": self.retention_days,
            "compliance_frameworks": self.compliance_frameworks or [],
            "routing_risk_weight": self.routing_risk_weight,
            "routing_cost_weight": self.routing_cost_weight,
            "routing_latency_weight": self.routing_latency_weight,
            "routing_priority_weight": self.routing_priority_weight,
            "default_data_sensitivity": self.default_data_sensitivity,
            "routing_enabled": self.routing_enabled,
        }


PLATFORM_GUARD_MODEL_NAMES = frozenset(
    {
        # Current user-facing platform model name (display: "ZeroShield Model").
        "zeroshield-model",
        # Legacy names kept so older registrations are still detected as platform
        # models (blocked from inference / hidden from kill-switch governance).
        "zeroshield-guard-120b",
        "bedrock-gpt-oss-120b",
        "bedrock-gpt-oss-120b-long-context",
    }
)


def _canonical_guard_model_name(name: str) -> str:
    """Fold a model name to its canonical comparison form.

    B2 (regression fix): the previous check only did ``.strip().lower()``,
    which folds CASE but not SEPARATORS — so the display variant
    ``"ZeroShield Model"`` (space) or ``"zeroshield_model"`` (underscore) did
    NOT match the canonical frozenset member ``"zeroshield-model"`` and could be
    isolated / re-surfaced in the user model-isolation UI. Mirror the gateway's
    ``_canonical_model_name`` (collapse any run of whitespace/underscore/hyphen
    to a single hyphen) so both planes agree on what is a platform model.
    """
    import re

    return re.sub(r"[\s_-]+", "-", (name or "").strip().lower()).strip("-")


def platform_guard_model_names() -> frozenset[str]:
    """Canonicalized model names reserved for ZeroShield scanning (not governance)."""
    names = {_canonical_guard_model_name(n) for n in PLATFORM_GUARD_MODEL_NAMES}
    env_name = _canonical_guard_model_name(
        os.getenv("ZEROSHIELD_GUARD_MODEL_NAME", "zeroshield-model")
    )
    if env_name:
        names.add(env_name)
    return frozenset(names)


def is_platform_managed_llm_provider(provider: str) -> bool:
    return (provider or "").strip().lower() == "internal"


def is_platform_managed_llm_model_name(model_name: str) -> bool:
    # B2: canonicalize the candidate the SAME way as the reserved set so
    # separator/whitespace variants ("ZeroShield Model", "zeroshield_model")
    # are caught, not just case variants.
    return _canonical_guard_model_name(model_name) in platform_guard_model_names()


_BEDROCK_FOUNDATION_PREFIXES = (
    "anthropic.",
    "global.anthropic",
    "global.amazon",
    "amazon.",
    "meta.",
    "openai.",
    "cohere.",
    "ai21.",
    "mistral.",
)


def is_bedrock_foundation_model_id(name: str) -> bool:
    """True for AWS Bedrock foundation model IDs (mirrors gateway platform_models)."""
    normalized = (name or "").strip().lower()
    return any(normalized.startswith(p) for p in _BEDROCK_FOUNDATION_PREFIXES)


def is_reserved_inference_model_name(model_name: str, model_id: str = "") -> bool:
    """True when a model must never enter org routing pools (mirrors gateway is_platform_model_name)."""
    if is_platform_managed_llm_model_name(model_name):
        return True
    for candidate in (model_name, model_id):
        normalized = (candidate or "").strip().lower()
        if not normalized:
            continue
        bare = normalized.split("/", 1)[1] if normalized.startswith("bedrock/") else normalized
        if is_bedrock_foundation_model_id(bare):
            return True
    return False


LLM_PROVIDER_CHOICES = [
    ("openai", "OpenAI"),
    ("anthropic", "Anthropic"),
    ("azure", "Azure OpenAI"),
    ("google", "Google AI"),
    ("aws_bedrock", "AWS Bedrock"),
    ("mistral", "Mistral AI"),
    ("cohere", "Cohere"),
    ("deepseek", "DeepSeek"),
    ("huggingface", "Hugging Face"),
    ("meta", "Meta / Llama"),
    ("ollama", "Ollama (Local)"),
    ("custom", "Custom / Other"),
]


DATA_SENSITIVITY_CHOICES = [
    ("public", "Public"),
    ("internal", "Internal"),
    ("confidential", "Confidential"),
    ("restricted", "Restricted"),
]

def _build_llm_model_key_cipher():
    configured = str(getattr(settings, LLM_MODEL_KEY_ENCRYPTION_ENV, "") or "").strip()
    fallback = str(getattr(settings, "SECRET_KEY", "") or "zeroshield-model-keys")
    return build_llm_model_key_cipher(configured_key=configured, fallback_secret=fallback)


class LLMModelConfig(models.Model):
    """
    Registered LLM model configuration for gateway routing via LiteLLM.

    Each entry maps a user-facing model_name to a LiteLLM-compatible
    model_id (e.g. ``openai/gpt-4o``, ``anthropic/claude-3-opus-20240229``).

    On save/delete, a ``post_save`` signal serializes all active models
    and pushes them to Redis (``llm:model_configs:{org_slug}``) so the
    gateway can hot-reload without restart.
    """

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="llm_model_configs",
    )
    provider = models.CharField(
        max_length=32,
        choices=LLM_PROVIDER_CHOICES,
        help_text="LLM provider (e.g. openai, anthropic, azure).",
    )
    model_name = models.CharField(
        max_length=128,
        help_text="User-facing model name used in API requests (e.g. gpt-4o).",
    )
    model_id = models.CharField(
        max_length=256,
        help_text="LiteLLM model identifier (e.g. openai/gpt-4o, anthropic/claude-3-opus-20240229).",
    )
    api_key_env_var = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Environment variable name for the API key (e.g. OPENAI_API_KEY).",
    )
    encrypted_api_key = models.TextField(
        blank=True,
        default="",
        help_text="Organization-provided provider API key encrypted at rest.",
    )
    api_key_last4 = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text="Last 4 characters of the stored API key for operator verification.",
    )
    api_base = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Custom API base URL (for Ollama, Azure, or self-hosted endpoints).",
    )
    region = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Cloud region (for AWS Bedrock, Azure, etc.).",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Only active models are routed by the gateway.",
    )

    # Dynamic routing fields (Module 1.5)
    data_sensitivity_level = models.CharField(
        max_length=32,
        choices=DATA_SENSITIVITY_CHOICES,
        default="public",
        help_text="Max data sensitivity this model is approved for.",
    )
    compliance_tags = models.JSONField(
        default=list,
        blank=True,
        help_text='Compliance frameworks this model meets (e.g. ["SOC2", "HIPAA"]).',
    )
    cost_per_1k_input_tokens = models.DecimalField(
        max_digits=10, decimal_places=6, default=0,
        help_text="Cost per 1K input tokens (USD).",
    )
    cost_per_1k_output_tokens = models.DecimalField(
        max_digits=10, decimal_places=6, default=0,
        help_text="Cost per 1K output tokens (USD).",
    )
    latency_sla_ms = models.PositiveIntegerField(
        default=30000,
        help_text="Latency SLA in milliseconds.",
    )
    risk_score = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Model risk score (0.0=safe, 1.0=highest risk). Updated from telemetry.",
    )
    routing_priority = models.PositiveIntegerField(
        default=0,
        help_text="Higher = preferred for auto-routing.",
    )
    rate_limit_rpm = models.PositiveIntegerField(
        default=0,
        help_text="Per-model requests per minute limit. 0 = no per-model limit.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider", "model_name"]
        verbose_name = "LLM Model Configuration"
        verbose_name_plural = "LLM Model Configurations"
        unique_together = [("organization", "model_name")]

    def __str__(self) -> str:
        status = "active" if self.is_active else "disabled"
        return f"{self.model_name} ({self.provider}, {status})"

    @property
    def api_key_set(self) -> bool:
        return bool(self.encrypted_api_key)

    @property
    def is_platform_managed(self) -> bool:
        """True for ZeroShield guard / internal scan models (hidden from governance UI)."""
        return is_platform_managed_llm_provider(self.provider) or is_platform_managed_llm_model_name(
            self.model_name
        )

    @classmethod
    def queryset_user_managed(cls, queryset):
        """Exclude platform-default guard models from org-facing CRUD APIs."""
        from django.db.models import Q

        guard_filter = Q(provider__iexact="internal")
        for name in platform_guard_model_names():
            guard_filter |= Q(model_name__iexact=name)
        return queryset.exclude(guard_filter)

    def set_api_key(self, raw_key: str) -> None:
        key = (raw_key or "").strip()
        if not key:
            self.encrypted_api_key = ""
            self.api_key_last4 = ""
            return
        cipher = _build_llm_model_key_cipher()
        self.encrypted_api_key = cipher.encrypt(key.encode("utf-8")).decode("utf-8")
        self.api_key_last4 = key[-4:] if len(key) >= 4 else key

    def get_api_key(self) -> str:
        if not self.encrypted_api_key:
            return ""
        cipher = _build_llm_model_key_cipher()
        try:
            return cipher.decrypt(self.encrypted_api_key.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # A NON-EMPTY blob that fails to decrypt = the key was encrypted under a
            # DIFFERENT cipher key (e.g. a prior DJANGO_SECRET_KEY rotation). This was
            # silently swallowed → treated as "no key" → the gateway routed anyway and
            # surfaced an opaque upstream 502 ("Missing credentials"). Log it so the
            # broken credential is VISIBLE and the model can be reconnected.
            logging.getLogger(__name__).warning(
                "LLM model %r (org=%s) has an UNDECRYPTABLE api_key (InvalidToken) — "
                "likely encrypted under a rotated key; treating as no key. Reconnect the model.",
                self.model_name, getattr(self, "organization_id", None),
            )
            return ""
        except Exception:
            logging.getLogger(__name__).warning(
                "LLM model %r (org=%s) api_key decrypt failed unexpectedly — treating as no key.",
                self.model_name, getattr(self, "organization_id", None),
            )
            return ""

    def has_usable_api_key(self) -> bool:
        """True only when a NON-EMPTY, DECRYPTABLE key is actually stored. The
        ``api_key_set`` property is just ``bool(encrypted_api_key)`` and stays True
        for a blob that no longer decrypts (rotated cipher key); routing-eligibility
        must use USABILITY, not mere presence, so a broken credential produces a
        clean 'no provider configured' error instead of an opaque upstream 502."""
        return bool(self.encrypted_api_key and self.get_api_key())

    def build_litellm_entry(self) -> dict[str, Any]:
        """Build a LiteLLM model_list entry for organization-owned inference."""
        from ai_mesh_shared.litellm_byok import normalize_litellm_params, resolve_bedrock_model_id

        provider_key = str(self.provider or "").lower()
        bedrock_region = (self.region or "").strip()
        model_id = self.model_id
        if provider_key == "aws_bedrock":
            if not bedrock_region:
                import os

                bedrock_region = (
                    os.environ.get("BEDROCK_REGION", "")
                    or os.environ.get("AWS_DEFAULT_REGION", "")
                ).strip()
            model_id = resolve_bedrock_model_id(self.model_id, region=bedrock_region)
        params: dict[str, Any] = {"model": model_id}
        if provider_key == "aws_bedrock":
            # Bedrock uses gateway AWS env credentials by default (see apply_bedrock_env_credentials).
            # Optional per-org BYOK override is stored encrypted when explicitly provided.
            if self.encrypted_api_key:
                params["api_key_encrypted"] = self.encrypted_api_key
            if self.region:
                params["aws_region_name"] = self.region
            else:
                import os

                default_region = (
                    os.environ.get("BEDROCK_REGION", "")
                    or os.environ.get("AWS_DEFAULT_REGION", "")
                ).strip()
                if default_region:
                    params["aws_region_name"] = default_region
        elif self.encrypted_api_key:
            params["api_key_encrypted"] = self.encrypted_api_key
        elif self.api_key_env_var:
            params["api_key"] = f"os.environ/{self.api_key_env_var}"
        if self.api_base:
            params["api_base"] = self.api_base
        params = normalize_litellm_params(params, provider=self.provider)
        return {
            "model_name": self.model_name,
            "provider": self.provider,
            "litellm_params": params,
        }

    def build_routing_payload(self) -> dict[str, Any]:
        """Routing metadata for gateway dynamic model selection."""
        return {
            "model_name": self.model_name,
            "model_id": self.model_id,
            "provider": self.provider,
            "data_sensitivity_level": self.data_sensitivity_level,
            "compliance_tags": self.compliance_tags or [],
            "cost_per_1k_input_tokens": float(self.cost_per_1k_input_tokens),
            "cost_per_1k_output_tokens": float(self.cost_per_1k_output_tokens),
            "latency_sla_ms": self.latency_sla_ms,
            "risk_score": self.risk_score,
            "routing_priority": self.routing_priority,
            "rate_limit_rpm": self.rate_limit_rpm,
            "is_active": self.is_active,
            # Usability-aware (NOT bare self.api_key_set): an undecryptable stored
            # blob must read as "no key" here so the gateway's routing-eligibility
            # excludes it and returns a clean 422, never an opaque upstream 502.
            "api_key_set": self.has_usable_api_key(),
            # BYOK-via-env: a model can carry its key by ENV-VAR REFERENCE
            # (api_key_env_var) instead of a stored encrypted key. The gateway
            # treats it as credentialed when that env var is present in its
            # environment — the "connect a key without persisting it" path.
            "api_key_env_var": self.api_key_env_var or "",
        }


# ── Model State (org-scoped, real-time) ────────────────────────────────

MODEL_STATUS_CHOICES = [
    ("active", "Active"),
    ("isolated", "Isolated"),
    ("degraded", "Degraded"),
]


class ModelState(models.Model):
    """
    Real-time per-model status within an organization.

    Every model that handles traffic MUST have a ModelState row.
    The gateway reads this from Redis (``model_state:{org_slug}:{model_name}``)
    to decide whether to allow, reroute, or block requests.

    Rolling risk score is computed from the last N requests'
    guardrail/anomaly/policy signals.
    """

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="model_states",
    )
    model_name = models.CharField(max_length=255, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=MODEL_STATUS_CHOICES,
        default="active",
        db_index=True,
    )
    # UNIT (M-25a): PERCENTAGE in [0.0, 100.0]. This is NOT the same scale as
    # GatewayAPIKey.risk_score, which is a FRACTION in [0.0, 1.0]. Never
    # compare or assign one to the other without an explicit conversion:
    #   fraction = model_state.risk_score / 100.0
    #   percent  = gateway_api_key.risk_score * 100.0
    # `threshold` below and KillSwitchAuditLog.risk_score use this same 0-100
    # scale; the gateway converts to fraction (/ 100.0) when it mixes a
    # ModelState verdict into fraction-scale telemetry (ai_mesh_gateway/main.py).
    risk_score = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text="Rolling weighted risk score (0–100).",
    )
    threshold = models.FloatField(
        default=80.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
        help_text="Risk score threshold that triggers isolation.",
    )
    action = models.CharField(
        max_length=20,
        choices=[("block", "Block"), ("reroute", "Reroute"), ("alert", "Alert Only")],
        default="block",
        help_text="Action to take when threshold is exceeded.",
    )
    fallback_model = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Fallback model for reroute action.",
    )
    rolling_window = models.JSONField(
        default=list,
        blank=True,
        help_text="Last N risk signal snapshots for rolling average.",
    )
    isolation_reason = models.TextField(blank=True, default="")
    isolated_at = models.DateTimeField(null=True, blank=True)
    isolated_until = models.DateTimeField(null=True, blank=True)
    cooldown_seconds = models.PositiveIntegerField(
        default=300,
        help_text="Cooldown period in seconds before auto-recovery.",
    )
    last_updated = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["model_name"]
        verbose_name = "Model State"
        verbose_name_plural = "Model States"
        unique_together = [("organization", "model_name")]

    def __str__(self) -> str:
        return f"ModelState({self.model_name}, {self.status}, risk={self.risk_score:.1f})"

    def clean(self) -> None:
        super().clean()
        if self.fallback_model and self.fallback_model == self.model_name:
            raise ValidationError({"fallback_model": "fallback_model must differ from model_name (self-loop)."})

    def build_redis_key(self) -> str:
        org_slug = self.organization.slug if self.organization_id else "default"
        return f"model_state:{org_slug}:{self.model_name}"

    def build_redis_payload(self) -> dict:
        return {
            "model_name": self.model_name,
            "status": self.status,
            "risk_score": self.risk_score,
            "threshold": self.threshold,
            "action": self.action,
            "fallback_model": self.fallback_model,
            "isolation_reason": self.isolation_reason,
            "isolated_at": self.isolated_at.isoformat() if self.isolated_at else None,
            "isolated_until": self.isolated_until.isoformat() if self.isolated_until else None,
            "cooldown_seconds": self.cooldown_seconds,
            "organization_id": self.organization_id,
            "org_slug": self.organization.slug if self.organization_id else "default",
        }


class KillSwitchAuditLog(models.Model):
    """
    Structured audit log for every kill-switch / model-isolation event.

    Provides full traceability: what happened, why, which request
    triggered it, and the resulting action.
    """

    organization = models.ForeignKey(
        "auth_api.Organization",
        on_delete=models.CASCADE,
        related_name="kill_switch_audit_logs",
    )
    event = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Event type, e.g. kill_switch_triggered, model_isolated, auto_recovery.",
    )
    model_name = models.CharField(max_length=255)
    risk_score = models.FloatField(default=0.0)
    threshold = models.FloatField(default=0.0)
    action = models.CharField(max_length=32, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    request_id = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    triggered_by = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="system / user email / auto_recovery",
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Kill-Switch Audit Log"
        verbose_name_plural = "Kill-Switch Audit Logs"

    def __str__(self) -> str:
        return f"KSAudit({self.event}, {self.model_name}, {self.timestamp})"


class PocSubmission(models.Model):
    """Stores every POC questionnaire submission for internal review."""

    SOURCE_CHOICES = [
        ("ai-mesh", "AI Mesh Firewall"),
    ]

    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    source = models.CharField(max_length=50, choices=SOURCE_CHOICES, default="ai-mesh", db_index=True)
    company = models.CharField(max_length=256, blank=True, default="")
    contact = models.CharField(max_length=256, blank=True, default="")
    poc_date = models.CharField(max_length=64, blank=True, default="")
    # Full JSON payload from the form
    raw_data = models.JSONField(default=dict)
    # Where the file landed in S3 (or a note if S3 was not configured)
    s3_key = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "POC Submission"
        verbose_name_plural = "POC Submissions"

    def __str__(self) -> str:
        return f"{self.company} — {self.contact} ({self.submitted_at:%Y-%m-%d %H:%M})"

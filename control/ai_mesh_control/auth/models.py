from django.conf import settings
from django.db import models
from django.utils import timezone


class Organization(models.Model):
    """Organization for multi-tenant data isolation."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=64, unique=True, help_text="Unique identifier for URLs/keys")
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(models.Model):
    """Role for RBAC (e.g. admin, analyst, viewer)."""

    name = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """Extended profile for User; holds roles and organization."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        primary_key=True,
    )
    display_name = models.CharField(max_length=255, blank=True)
    timezone = models.CharField(max_length=64, default="UTC")
    preferences = models.JSONField(default=dict, blank=True)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="user_profiles",
    )
    roles = models.ManyToManyField(Role, related_name="user_profiles", blank=True)
    # Dedicated cross-org marker. Org admins and superusers are ORG-scoped only;
    # ONLY a platform operator (is_staff AND this flag) may target another org via
    # ?organization_id. is_superuser alone must NEVER grant global cross-org access.
    is_platform_operator = models.BooleanField(default=False)
    # Set whenever the user's password changes. Access tokens minted before this
    # instant (iat < password_changed_at) are rejected at authentication time so a
    # token cannot outlive the password it was issued under (full TTL otherwise).
    password_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name or self.user.get_username()


def access_token_predates_password_change(user, validated_token) -> bool:
    """True if the access token was issued BEFORE the user's last password change.

    Lets the authentication backend reject an access token whose `iat` is older
    than profile.password_changed_at, so a token cannot outlive the password it
    was minted under. Fail OPEN (return False) on any error/missing data — the
    refresh-token blacklist remains the hard remediation; this is defense in depth.
    """
    try:
        from datetime import datetime, timezone as _dt_timezone

        changed_at = getattr(getattr(user, "profile", None), "password_changed_at", None)
        if changed_at is None:
            return False
        iat = validated_token.payload.get("iat")
        if iat is None:
            return False
        token_issued = datetime.fromtimestamp(int(iat), tz=_dt_timezone.utc)
        return token_issued < changed_at
    except Exception:
        return False


def is_platform_operator(user) -> bool:
    """True only for a dedicated platform operator (is_staff AND profile flag).

    Platform operators are the sole identity allowed to act across organizations
    (e.g. honoring ?organization_id). A plain superuser / org admin is ORG-scoped
    only and must NOT receive global cross-org access. Fail closed on any error.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_staff", False):
        return False
    try:
        return bool(user.profile.is_platform_operator)
    except Exception:
        return False


class TerminatedSession(models.Model):
    """User session termination: user_id is blocked from further requests until cleared."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="terminated_sessions",
    )
    reason = models.CharField(max_length=255, blank=True)
    terminated_at = models.DateTimeField(default=timezone.now)
    terminated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions_terminated",
    )
    # When set, the termination has been lifted (reinstated) and no longer blocks
    # the user. Kept (rather than deleting the row) to preserve the audit trail.
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions_reinstated",
    )

    class Meta:
        ordering = ["-terminated_at"]

    def __str__(self):
        return f"User {self.user_id} terminated @ {self.terminated_at}"

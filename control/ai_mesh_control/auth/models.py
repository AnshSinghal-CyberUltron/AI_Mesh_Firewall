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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name or self.user.get_username()


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

    class Meta:
        ordering = ["-terminated_at"]

    def __str__(self):
        return f"User {self.user_id} terminated @ {self.terminated_at}"

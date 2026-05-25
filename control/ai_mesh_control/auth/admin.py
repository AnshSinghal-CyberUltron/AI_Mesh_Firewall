from django.contrib import admin

from .models import Organization, Role, TerminatedSession, UserProfile


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(TerminatedSession)
class TerminatedSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "reason", "terminated_at", "terminated_by")
    list_filter = ("terminated_at",)
    raw_id_fields = ("user", "terminated_by")
    readonly_fields = ("terminated_at",)
    ordering = ["-terminated_at"]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "organization", "timezone")
    list_filter = ("roles", "organization")
    filter_horizontal = ("roles",)
    raw_id_fields = ("organization",)

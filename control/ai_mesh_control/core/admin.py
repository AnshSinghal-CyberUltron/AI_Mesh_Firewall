from django.contrib import admin

from core.models import Agent, Endpoint, GatewayAPIKey, OrganizationAgentKey, OrganizationUpstreamCa, PocSubmission


@admin.register(Endpoint)
class EndpointAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "identifier", "organization", "status", "last_seen_at")
    list_filter = ("status", "organization")
    search_fields = ("name", "identifier")
    raw_id_fields = ("organization",)
    readonly_fields = ("created_at", "updated_at")

    def delete_queryset(self, request, queryset):
        """Unregister: delete all agents linked to these endpoints, then delete endpoints."""
        for endpoint in queryset:
            Agent.objects.filter(endpoint=endpoint).delete()
        super().delete_queryset(request, queryset)

    def delete_model(self, request, obj):
        Agent.objects.filter(endpoint=obj).delete()
        super().delete_model(request, obj)


@admin.register(OrganizationAgentKey)
class OrganizationAgentKeyAdmin(admin.ModelAdmin):
    list_display = ("prefix", "name", "organization", "is_active", "created_at")
    list_filter = ("is_active", "organization")
    search_fields = ("name", "prefix")
    raw_id_fields = ("organization",)
    readonly_fields = ("prefix", "key_hash", "created_at", "updated_at")


@admin.register(OrganizationUpstreamCa)
class OrganizationUpstreamCaAdmin(admin.ModelAdmin):
    list_display = ("organization", "sha256", "size_bytes", "uploaded_at", "uploaded_by")
    raw_id_fields = ("organization", "uploaded_by")
    readonly_fields = ("sha256", "size_bytes", "uploaded_at")

    def has_add_permission(self, request):
        return False


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "agent_type", "status", "endpoint", "user_id", "updated_at")
    list_filter = ("agent_type", "status")
    search_fields = ("name",)
    raw_id_fields = ("endpoint",)
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(GatewayAPIKey)
class GatewayAPIKeyAdmin(admin.ModelAdmin):
    list_display = (
        "prefix",
        "name",
        "project_id",
        "is_active",
        "risk_score",
        "rate_limit_tokens_per_minute",
        "created_at",
    )
    list_filter = ("is_active", "project_id")
    search_fields = ("name", "prefix", "project_id")
    readonly_fields = (
        "id",
        "prefix",
        "key_hash",
        "created_at",
        "updated_at",
        "last_used_at",
    )
    fieldsets = (
        (
            "Identity",
            {
                "fields": ("id", "prefix", "key_hash", "name", "owner", "project_id"),
            },
        ),
        (
            "Permissions & Limits",
            {
                "fields": ("permissions", "allowed_models", "rate_limit_tokens_per_minute", "risk_score"),
            },
        ),
        (
            "Status",
            {
                "fields": ("is_active", "expires_at", "last_used_at"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(PocSubmission)
class PocSubmissionAdmin(admin.ModelAdmin):
    list_display = ("submitted_at", "source", "company", "contact", "poc_date", "s3_key")
    list_filter = ("submitted_at", "source")
    search_fields = ("company", "contact")
    readonly_fields = ("submitted_at", "source", "company", "contact", "poc_date", "s3_key", "raw_data")
    ordering = ("-submitted_at",)

    def has_add_permission(self, request):
        return False  # submissions come only from the form

    def has_change_permission(self, request, obj=None):
        return False  # read-only view

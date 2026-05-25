from django.contrib import admin

from .models import ComplianceViolation, EnforcementEvent, Policy, PolicyVersion, Rule
from .vector_models import VectorCollectionPolicy


class RuleInline(admin.TabularInline):
    model = Rule
    extra = 0
    ordering = ["-priority", "id"]


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "severity", "enabled", "priority", "updated_at")
    list_filter = ("enabled", "severity", "category")
    search_fields = ("code", "name", "category")
    ordering = ["-priority", "code"]
    inlines = [RuleInline]


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("name", "policy", "rule_type", "action", "enabled", "priority", "updated_at")
    list_filter = ("action", "rule_type", "enabled")
    search_fields = ("name", "policy__code")
    raw_id_fields = ("policy",)
    ordering = ["policy", "-priority"]


@admin.register(PolicyVersion)
class PolicyVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "policy", "version", "created_at", "created_by")
    list_filter = ("policy",)
    raw_id_fields = ("policy", "created_by")
    readonly_fields = ("snapshot", "created_at")
    ordering = ["-created_at"]


@admin.register(EnforcementEvent)
class EnforcementEventAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "policy", "rule", "user_id", "endpoint_id", "created_at")
    list_filter = ("action",)
    search_fields = ("policy__code",)
    raw_id_fields = ("policy", "rule")
    readonly_fields = ("created_at",)
    ordering = ["-created_at"]


@admin.register(ComplianceViolation)
class ComplianceViolationAdmin(admin.ModelAdmin):
    list_display = ("id", "framework", "violation_type", "severity", "status", "enforcement_event", "created_at")
    list_filter = ("framework", "status", "severity")
    search_fields = ("framework", "violation_type", "description")
    raw_id_fields = ("enforcement_event",)
    readonly_fields = ("created_at",)
    ordering = ["-created_at"]


@admin.register(VectorCollectionPolicy)
class VectorCollectionPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "project_id",
        "collection_name",
        "vector_db_type",
        "default_action",
        "enabled",
        "updated_at",
    )
    list_filter = ("vector_db_type", "default_action", "enabled", "require_context_scan")
    search_fields = ("name", "project_id", "collection_name")
    ordering = ["-created_at"]
    readonly_fields = ("id", "created_at", "updated_at")

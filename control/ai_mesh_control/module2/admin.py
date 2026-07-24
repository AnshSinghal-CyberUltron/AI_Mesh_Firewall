from django.contrib import admin

from module2.models import AlertFiring, AlertRule, AnomalyRule, Playbook, PlaybookRun, ThreatIntelEntry


@admin.register(Playbook)
class PlaybookAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "organization", "enabled", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("name",)


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "metric", "operator", "threshold", "severity", "enabled", "organization")
    list_filter = ("metric", "severity", "enabled")


@admin.register(AlertFiring)
class AlertFiringAdmin(admin.ModelAdmin):
    list_display = ("id", "rule", "fired_at", "resolved_at", "current_value")
    list_filter = ("fired_at",)


@admin.register(PlaybookRun)
class PlaybookRunAdmin(admin.ModelAdmin):
    list_display = ("id", "playbook", "trigger", "status", "started_at", "finished_at")


@admin.register(AnomalyRule)
class AnomalyRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "scope", "scope_id", "metric", "z_score_threshold", "enabled", "organization")


@admin.register(ThreatIntelEntry)
class ThreatIntelEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "threat_type", "source", "confidence", "auto_block", "organization", "created_at")
    list_filter = ("source", "auto_block")

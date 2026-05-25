from django.contrib import admin

from .models import GuardrailProfile, MCPServerRegistration


@admin.register(MCPServerRegistration)
class MCPServerRegistrationAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "transport", "is_active", "created_at"]
    list_filter = ["transport", "is_active"]
    search_fields = ["name", "url"]


@admin.register(GuardrailProfile)
class GuardrailProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "is_default", "created_at"]
    list_filter = ["is_default"]
    search_fields = ["name"]

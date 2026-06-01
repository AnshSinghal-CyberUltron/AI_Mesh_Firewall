from django.contrib import admin

from .models import MCPServerRegistration


@admin.register(MCPServerRegistration)
class MCPServerRegistrationAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "transport", "is_active", "created_at"]
    list_filter = ["transport", "is_active"]
    search_fields = ["name", "url"]

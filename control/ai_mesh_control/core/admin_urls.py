"""URL routing for admin portal API (/api/admin/)."""

from django.urls import path

from core.admin_views import (
    AdminAgentVersionStatsView,
    AdminConfigView,
    AdminEndpointHealthView,
)

urlpatterns = [
    path("agent-version-stats/", AdminAgentVersionStatsView.as_view(), name="admin-agent-version-stats"),
    path("endpoint-health/", AdminEndpointHealthView.as_view(), name="admin-endpoint-health"),
    path("config/", AdminConfigView.as_view(), name="admin-config"),
]

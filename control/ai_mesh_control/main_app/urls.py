"""
URL configuration — AI Mesh Firewall control plane (no device/agent distribution).
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from core.views import health_view, services_health_view
from core.poc_views import poc_questionnaire_page, poc_questionnaire_submit
from core.alertmanager_webhook_views import alertmanager_webhook_view
from main_app.schema_views import docs_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/gateways/", include("core.gateway_urls")),
    path("api/kill-switches/", include("core.kill_switch_urls")),
    path("api/webhooks/alertmanager/", alertmanager_webhook_view, name="alertmanager-webhook"),
    path("api/auth/", include("auth.urls")),
    path("api/health/", health_view),
    path("api/health/services/", services_health_view),
    path("api/policies/", include("policy.urls")),
    path("api/vector-policies/", include("policy.vector_urls")),
    path("api/vector-providers/", include("policy.vector_provider_urls")),
    path("api/policy/", include("policy.evaluation_urls")),
    path("api/security/", include("policy.security_urls")),
    path("api/notifications/", include("policy.notification_urls")),
    path("api/ingestion/", include("core.ingestion_urls")),
    path("api/dashboard/", include("core.dashboard_urls")),
    path("api/firewall/config/", include("core.firewall_config_urls")),
    path("api/firewall/models/", include("core.llm_model_urls")),
    path("api/models/", include("core.model_state_urls")),
    path("api/mcp-connector/", include("mcp_connector.urls")),
    path("api/admin/", include("core.admin_urls")),
    path("ai-mesh-poc", poc_questionnaire_page, name="poc-questionnaire-page"),
    path("api/poc-questionnaire", poc_questionnaire_submit, name="poc-questionnaire-submit"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", docs_view, name="docs"),
]

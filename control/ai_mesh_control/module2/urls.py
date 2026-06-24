from django.urls import path
from rest_framework.routers import DefaultRouter

from module2.views import (
    IncidentDetailView,
    IncidentListView,
    McpRiskView,
    ModelExposureView,
    RagHealthView,
    ThreatIntelSyncView,
    ThreatIntelTelemetryView,
    ThreatIntelViewSet,
    UebaApiKeyBehaviorView,
    UebaApiKeyReassessView,
    UebaApiKeyRegistryView,
    UebaApiKeySettingsView,
    UebaApiKeySummaryView,
    UebaApiKeyTimelineView,
    UebaLearningKeysView,
    UebaOrgSettingsView,
    UnifiedDashboardView,
)

router = DefaultRouter()
router.register(r"threat-intel", ThreatIntelViewSet, basename="module2-threat-intel")

urlpatterns = [
    path("dashboard/", UnifiedDashboardView.as_view(), name="module2-dashboard"),
    path("ueba/settings/", UebaOrgSettingsView.as_view(), name="module2-ueba-settings"),
    path("ueba/api-keys/learning/", UebaLearningKeysView.as_view(), name="module2-ueba-learning"),
    path("ueba/api-keys/<uuid:key_id>/settings/", UebaApiKeySettingsView.as_view(), name="module2-ueba-key-settings"),
    path("ueba/api-keys/<uuid:key_id>/reassess/", UebaApiKeyReassessView.as_view(), name="module2-ueba-reassess"),
    path("ueba/api-keys/summary/", UebaApiKeySummaryView.as_view(), name="module2-ueba-summary"),
    path("ueba/api-keys/timeline/", UebaApiKeyTimelineView.as_view(), name="module2-ueba-timeline"),
    path("ueba/api-keys/registry/", UebaApiKeyRegistryView.as_view(), name="module2-ueba-registry"),
    path("ueba/api-keys/<uuid:key_id>/behavior/", UebaApiKeyBehaviorView.as_view(), name="module2-ueba-behavior"),
    path("models/exposure/", ModelExposureView.as_view(), name="module2-models-exposure"),
    path("threat-intel/telemetry/", ThreatIntelTelemetryView.as_view(), name="module2-threat-intel-telemetry"),
    path("threat-intel/sync/", ThreatIntelSyncView.as_view(), name="module2-threat-intel-sync"),
    path("rag/health/", RagHealthView.as_view(), name="module2-rag-health"),
    path("mcp/risk/", McpRiskView.as_view(), name="module2-mcp-risk"),
    path("incidents/", IncidentListView.as_view(), name="module2-incident-list"),
    path("incidents/<int:pk>/", IncidentDetailView.as_view(), name="module2-incident-detail"),
] + router.urls

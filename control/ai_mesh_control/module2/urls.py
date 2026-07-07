from django.urls import path
from rest_framework.routers import DefaultRouter

from module2.views import (
    IncidentBulkResolveView,
    IncidentDetailView,
    IncidentListView,
    McpRiskView,
    ModelExposureView,
    RagHealthView,
    ThreatIntelSyncView,
    ThreatIntelTelemetryView,
    ThreatIntelViewSet,
    UebaApiKeyBehaviorView,
    UebaApiKeyBundleView,
    UebaApiKeyRegistryView,
    UebaApiKeySummaryView,
    UebaApiKeyTimelineView,
    UebaRiskCalculationView,
    UnifiedDashboardView,
)

router = DefaultRouter()
router.register(r"threat-intel", ThreatIntelViewSet, basename="module2-threat-intel")

urlpatterns = [
    path("dashboard/", UnifiedDashboardView.as_view(), name="module2-dashboard"),
    path("ueba/api-keys/bundle/", UebaApiKeyBundleView.as_view(), name="module2-ueba-bundle"),
    path("ueba/api-keys/summary/", UebaApiKeySummaryView.as_view(), name="module2-ueba-summary"),
    path("ueba/api-keys/timeline/", UebaApiKeyTimelineView.as_view(), name="module2-ueba-timeline"),
    path("ueba/api-keys/registry/", UebaApiKeyRegistryView.as_view(), name="module2-ueba-registry"),
    path("ueba/api-keys/<uuid:key_id>/behavior/", UebaApiKeyBehaviorView.as_view(), name="module2-ueba-behavior"),
    path("ueba/risk-calculation/", UebaRiskCalculationView.as_view(), name="module2-ueba-risk-calculation"),
    path("models/exposure/", ModelExposureView.as_view(), name="module2-models-exposure"),
    path("threat-intel/telemetry/", ThreatIntelTelemetryView.as_view(), name="module2-threat-intel-telemetry"),
    path("threat-intel/sync/", ThreatIntelSyncView.as_view(), name="module2-threat-intel-sync"),
    path("rag/health/", RagHealthView.as_view(), name="module2-rag-health"),
    path("mcp/risk/", McpRiskView.as_view(), name="module2-mcp-risk"),
    path("incidents/bulk-resolve/", IncidentBulkResolveView.as_view(), name="module2-incident-bulk-resolve"),
    path("incidents/", IncidentListView.as_view(), name="module2-incident-list"),
    path("incidents/<int:pk>/", IncidentDetailView.as_view(), name="module2-incident-detail"),
] + router.urls

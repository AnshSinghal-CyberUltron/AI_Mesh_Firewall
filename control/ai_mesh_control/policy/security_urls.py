from django.urls import path

from .evaluation_views import SecurityScanView

from .review_views import (
    ReviewApproveView,
    ReviewQueueListView,
    ReviewRejectView,
    SecurityIncidentEscalateView,
    SecurityIncidentListView,
    SecurityIncidentResolveView,
)

from .security_views import (
    AgentTypeStatsView,
    AttackCatalogView,
    AttackGraphView,
    AttackVectorTrendsView,
    BlockageTrendView,
    EnforcementActionStatsView,
    EscalateIncidentView,
    ModuleChartsView,
    ModuleKpisView,
    ModuleTrendsView,
    OwaspEventsView,
    OwaspStatsView,
    RAGPipelineStageKpisView,
    RAGPipelineTraceView,
    ResolveIncidentView,
    SocKpisView,
    ThreatFeedView,
    ThreatSourcesView,
    UsagePatternsView,
    UserBlockageKpisView,
    UserBlockageStatsView,
    ViolationCategoriesView,
)

urlpatterns = [
    path("attack-catalog/", AttackCatalogView.as_view(), name="security-attack-catalog"),
    path("scan/", SecurityScanView.as_view(), name="security-scan"),
    path("threat-feed/", ThreatFeedView.as_view(), name="security-threat-feed"),
    path("attack-vector-trends/", AttackVectorTrendsView.as_view(), name="security-attack-vector-trends"),
    path("owasp-stats/", OwaspStatsView.as_view(), name="security-owasp-stats"),
    path("owasp-events/", OwaspEventsView.as_view(), name="security-owasp-events"),
    path("threat-sources/", ThreatSourcesView.as_view(), name="security-threat-sources"),
    path("attack-graph/", AttackGraphView.as_view(), name="security-attack-graph"),
    path("soc-kpis/", SocKpisView.as_view(), name="security-soc-kpis"),
    path("module-kpis/", ModuleKpisView.as_view(), name="security-module-kpis"),
    path("module-trends/", ModuleTrendsView.as_view(), name="security-module-trends"),
    path("module-charts/<str:module_id>/", ModuleChartsView.as_view(), name="security-module-charts"),
    path("incidents/<int:pk>/escalate/", EscalateIncidentView.as_view(), name="incident-escalate"),
    path("incidents/<int:pk>/resolve/", ResolveIncidentView.as_view(), name="incident-resolve"),

    # Human Review Queue + SecurityIncident-model SOC/HITL surface (policy.review_views).
    # NOTE: escalate/resolve here operate on the SecurityIncident model and are mounted
    # under -incident/ suffixes to avoid colliding with the EnforcementEvent-based
    # incidents/<pk>/escalate|resolve/ routes above (those have fail-closed tenant
    # scoping, admin gating, and notifications and must remain the canonical ones).
    path("review-queue/", ReviewQueueListView.as_view(), name="security-review-queue"),
    path("review-queue/<int:pk>/approve/", ReviewApproveView.as_view(), name="security-review-approve"),
    path("review-queue/<int:pk>/reject/", ReviewRejectView.as_view(), name="security-review-reject"),
    path("incidents/", SecurityIncidentListView.as_view(), name="security-incident-list"),
    path("incidents/<int:pk>/escalate-incident/", SecurityIncidentEscalateView.as_view(), name="security-incident-escalate"),
    path("incidents/<int:pk>/resolve-incident/", SecurityIncidentResolveView.as_view(), name="security-incident-resolve"),
    path("user-blockage-kpis/", UserBlockageKpisView.as_view(), name="security-user-blockage-kpis"),
    path("blockage-trend/", BlockageTrendView.as_view(), name="security-blockage-trend"),
    path("usage-patterns/", UsagePatternsView.as_view(), name="security-usage-patterns"),
    path("violation-categories/", ViolationCategoriesView.as_view(), name="security-violation-categories"),
    path("agent-type-stats/", AgentTypeStatsView.as_view(), name="security-agent-type-stats"),
    path("enforcement-action-stats/", EnforcementActionStatsView.as_view(), name="security-enforcement-action-stats"),
    path("user-blockage-stats/", UserBlockageStatsView.as_view(), name="security-user-blockage-stats"),

    path("rag-pipeline-kpis/", RAGPipelineStageKpisView.as_view(), name="security-rag-pipeline-kpis"),
    path("rag-pipeline-trace/<str:request_id>/", RAGPipelineTraceView.as_view(), name="security-rag-pipeline-trace"),
]

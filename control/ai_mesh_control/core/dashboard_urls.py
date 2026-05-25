"""URL routing for dashboard API."""

from django.urls import path

from .dashboard_views import (
    AIServicesView,
    ComplianceSummaryView,
    DashboardAgentsView,
  DashboardReportView,
    DashboardSummaryView,
    ModelUsageView,
    RiskDistributionView,
)

urlpatterns = [
    path("summary/", DashboardSummaryView.as_view(), name="dashboard-summary"),
  path("report/", DashboardReportView.as_view(), name="dashboard-report"),
    path("agents/", DashboardAgentsView.as_view(), name="dashboard-agents"),
    path("compliance/", ComplianceSummaryView.as_view(), name="dashboard-compliance"),
    path("model-usage/", ModelUsageView.as_view(), name="dashboard-model-usage"),
    path("ai-services/", AIServicesView.as_view(), name="dashboard-ai-services"),
    path("risk-distribution/", RiskDistributionView.as_view(), name="dashboard-risk-distribution"),
]

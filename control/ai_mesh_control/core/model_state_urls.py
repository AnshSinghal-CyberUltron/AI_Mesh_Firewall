"""URL patterns for ModelState and KillSwitch audit log."""

from django.urls import path

from core.model_state_views import (
    KillSwitchAuditLogView,
    ModelIsolateView,
    ModelRecoverView,
    ModelStatusDetailView,
    ModelStatusListView,
    ModelStatusSyncView,
)

urlpatterns = [
    path("sync/", ModelStatusSyncView.as_view(), name="model-status-sync"),
    path("status/", ModelStatusListView.as_view(), name="model-status-list"),
    path("status/<str:model_name>/", ModelStatusDetailView.as_view(), name="model-status-detail"),
    path("isolate/", ModelIsolateView.as_view(), name="model-isolate"),
    path("recover/<str:model_name>/", ModelRecoverView.as_view(), name="model-recover"),
    path("audit-log/", KillSwitchAuditLogView.as_view(), name="kill-switch-audit-log"),
]

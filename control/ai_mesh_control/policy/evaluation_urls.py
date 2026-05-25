from django.urls import path

from .analytics_views import TopRulesView
from .evaluation_views import EnforcementEventBatchView, PolicyCheckView

urlpatterns = [
    path("check/", PolicyCheckView.as_view(), name="policy-check"),
    path("top-rules/", TopRulesView.as_view(), name="policy-top-rules"),
    path("enforcement-events/", EnforcementEventBatchView.as_view(), name="enforcement-events"),
]

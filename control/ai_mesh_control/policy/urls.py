from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .analytics_views import PolicyAnalyticsView, TopViolatorsView
from .compile_views import PolicyCompileStatusView, PolicyCompileView
from .evaluation_views import PolicyTestView
from .views import PolicyViewSet, RuleViewSet

router = DefaultRouter()
router.register(r"rules", RuleViewSet, basename="rule")
router.register(r"", PolicyViewSet, basename="policy")

urlpatterns = [
    path("analytics/", PolicyAnalyticsView.as_view(), name="policy-analytics"),
    path("top-violators/", TopViolatorsView.as_view(), name="policy-top-violators"),
    path("test/", PolicyTestView.as_view(), name="policy-test"),
    path("compile/", PolicyCompileView.as_view(), name="policy-compile"),
    path("compile/status/", PolicyCompileStatusView.as_view(), name="policy-compile-status"),
    path("", include(router.urls)),
]

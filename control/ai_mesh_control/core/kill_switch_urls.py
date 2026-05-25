"""URL routing for kill-switch API (/api/kill-switches/)."""

from rest_framework.routers import DefaultRouter

from core.kill_switch_views import KillSwitchViewSet

router = DefaultRouter()
router.register(r"", KillSwitchViewSet, basename="kill-switch")

urlpatterns = router.urls

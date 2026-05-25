"""URL routing for gateway API (/api/gateways/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.gateway_key_views import GatewayAPIKeyViewSet
from core.gateway_instance_views import (
    GatewayInstanceRegisterView,
    GatewayInstanceTelemetryView,
    GatewayPublicUrlView,
)
from core.gateway_views import GatewayStatsListView, SimulatorDefaultGatewayKeyView

router = DefaultRouter()
router.register(r"keys", GatewayAPIKeyViewSet, basename="gateway-apikey")

urlpatterns = [
    path("public-url/", GatewayPublicUrlView.as_view(), name="gateway-public-url"),
    path("instances/register/", GatewayInstanceRegisterView.as_view(), name="gateway-instance-register"),
    path("instances/telemetry/", GatewayInstanceTelemetryView.as_view(), name="gateway-instance-telemetry"),
    path("stats/", GatewayStatsListView.as_view(), name="gateway-stats-list"),
    path("simulator-default/", SimulatorDefaultGatewayKeyView.as_view(), name="gateway-simulator-default-key"),
    path("", include(router.urls)),
]

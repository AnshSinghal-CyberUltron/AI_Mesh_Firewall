"""URL routing for admin portal API (/api/admin/)."""

from django.urls import path

from core.admin_views import (
    AdminAgentVersionStatsView,
    AdminConfigView,
    AdminEndpointHealthView,
)
from core.redis_kill_switch_views import RedisKillSwitchValidateView
from core.gateway_admin_proxy_views import (
    CircuitBreakerResetProxyView,
    CircuitBreakerStateProxyView,
    CircuitBreakerTriggerProxyView,
    GatewayRagCollectionsProxyView,
)

urlpatterns = [
    path("agent-version-stats/", AdminAgentVersionStatsView.as_view(), name="admin-agent-version-stats"),
    path("endpoint-health/", AdminEndpointHealthView.as_view(), name="admin-endpoint-health"),
    path("config/", AdminConfigView.as_view(), name="admin-config"),
    # Phase 1 Fx-2b: server-side proxy for gateway /v1/admin/* circuit breaker
    # endpoints. Lets the admin UI consume admin reads/mutations via Django
    # RBAC (IsAdminOrSuperuser) rather than minting admin gateway API keys.
    path(
        "gateway/circuit-breaker/state/",
        CircuitBreakerStateProxyView.as_view(),
        name="admin-gateway-cb-state",
    ),
    path(
        "gateway/circuit-breaker/trigger/",
        CircuitBreakerTriggerProxyView.as_view(),
        name="admin-gateway-cb-trigger",
    ),
    path(
        "gateway/circuit-breaker/reset/",
        CircuitBreakerResetProxyView.as_view(),
        name="admin-gateway-cb-reset",
    ),
    # Phase 1 F-3.1: server-side proxy for gateway /v1/admin/rag/collections
    # (admin variant of the per-tenant /v1/rag/collections endpoint).
    # Django stamps project_id from the caller's organization so the browser
    # cannot impersonate another tenant.
    path(
        "gateway/rag/collections/",
        GatewayRagCollectionsProxyView.as_view(),
        name="admin-gateway-rag-collections",
    ),
    path(
        "redis/kill-switches/validate/",
        RedisKillSwitchValidateView.as_view(),
        name="admin-redis-kill-switch-validate",
    ),
]

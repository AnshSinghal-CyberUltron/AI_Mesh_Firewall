"""URL routing for vector provider config API (/api/vector-providers/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from policy.vector_provider_views import VectorProviderConfigViewSet

router = DefaultRouter()
router.register(r"", VectorProviderConfigViewSet, basename="vector-provider-config")

urlpatterns = [
    path("", include(router.urls)),
]

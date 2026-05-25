"""URL routing for vector policy API (/api/vector-policies/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from policy.vector_views import VectorCollectionPolicyViewSet, VectorPolicyCompileView

router = DefaultRouter()
router.register(r"", VectorCollectionPolicyViewSet, basename="vector-collection-policy")

urlpatterns = [
    path("compile/", VectorPolicyCompileView.as_view(), name="vector-policy-compile"),
    path("", include(router.urls)),
]

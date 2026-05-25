"""URL routing for LLMModelConfig API."""

from django.urls import path

from core.llm_model_views import LLMModelConfigDetailView, LLMModelConfigListView

urlpatterns = [
    path("", LLMModelConfigListView.as_view(), name="llm-model-list"),
    path("<int:pk>/", LLMModelConfigDetailView.as_view(), name="llm-model-detail"),
]

"""URL routing for ingestion API."""

from django.urls import path

from .ingestion_views import IngestionBatchView, IngestionEventView

urlpatterns = [
    path("events/", IngestionEventView.as_view(), name="ingestion-events"),
    path("events/batch/", IngestionBatchView.as_view(), name="ingestion-events-batch"),
]

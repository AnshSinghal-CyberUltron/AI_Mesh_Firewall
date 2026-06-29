from django.urls import path

from module3.views import (
    ClusterHeartbeatIngestView,
    EmbeddingInspectionIngestView,
    K8sFirewallEmbeddingQueueView,
    K8sFirewallNetworkEventsView,
    K8sFirewallSummaryView,
    K8sFirewallTopologyView,
    LlmopsAdmissionLogView,
    LlmopsArtifactsView,
    LlmopsPipelineRunsView,
    LlmopsSummaryView,
    LlmopsVerifyView,
    Module3SimulatorIngestView,
    NetworkEventIngestView,
)

urlpatterns = [
    path("llmops/summary/", LlmopsSummaryView.as_view(), name="module3-llmops-summary"),
    path("llmops/artifacts/", LlmopsArtifactsView.as_view(), name="module3-llmops-artifacts"),
    path("llmops/admission-log/", LlmopsAdmissionLogView.as_view(), name="module3-llmops-admission-log"),
    path("llmops/pipeline-runs/", LlmopsPipelineRunsView.as_view(), name="module3-llmops-pipeline-runs"),
    path("llmops/verify/", LlmopsVerifyView.as_view(), name="module3-llmops-verify"),
    path("k8s-firewall/summary/", K8sFirewallSummaryView.as_view(), name="module3-k8s-summary"),
    path("k8s-firewall/topology/", K8sFirewallTopologyView.as_view(), name="module3-k8s-topology"),
    path("k8s-firewall/network-events/", K8sFirewallNetworkEventsView.as_view(), name="module3-k8s-network"),
    path("k8s-firewall/embedding-queue/", K8sFirewallEmbeddingQueueView.as_view(), name="module3-k8s-embedding"),
    path("ingest/cluster-heartbeat/", ClusterHeartbeatIngestView.as_view(), name="module3-ingest-heartbeat"),
    path("ingest/network-event/", NetworkEventIngestView.as_view(), name="module3-ingest-network"),
    path("ingest/embedding-inspection/", EmbeddingInspectionIngestView.as_view(), name="module3-ingest-embedding"),
    path("simulator/ingest/", Module3SimulatorIngestView.as_view(), name="module3-simulator-ingest"),
]

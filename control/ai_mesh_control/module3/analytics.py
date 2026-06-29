"""Aggregation helpers for Module 3 LLMOps and K8s firewall pages."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone

from module2.analytics import hours_from_period, paginate_queryset
from module3.models import (
    AdmissionDecision,
    ClusterRegistration,
    EmbeddingInspectionJob,
    ModelArtifact,
    NetworkPolicyEvent,
    PipelineRun,
    WorkloadPod,
)


def _since(period: str):
    return timezone.now() - timedelta(hours=hours_from_period(period))


def _paginate(qs, page: int, page_size: int):
    total, page_qs, page, page_size = paginate_queryset(qs, page, page_size)
    total_pages = (total + page_size - 1) // page_size if page_size else 1
    return page_qs, {
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def build_llmops_summary(org, period: str = "24h") -> dict:
    since = _since(period)
    artifacts = ModelArtifact.objects.filter(organization=org)
    total_artifacts = artifacts.count()
    signed = artifacts.filter(signature_status="valid").count()
    signed_pct = round(signed / total_artifacts * 100, 1) if total_artifacts else 0.0

    decisions = AdmissionDecision.objects.filter(organization=org, created_at__gte=since)
    blocked = decisions.filter(result="deny").count()
    allowed = decisions.filter(result="allow").count()

    integrity_failures = artifacts.filter(
        Q(signature_status="invalid") | Q(data_sha256="") | Q(model_sha256="")
    ).count()

    runs = PipelineRun.objects.filter(organization=org, started_at__gte=since)
    pipeline_runs = runs.count()
    failed_runs = runs.filter(status="failed").count()

    avg_latency = decisions.aggregate(avg=Avg("latency_ms"))["avg"] or 0

    return {
        "period": period,
        "total_artifacts": total_artifacts,
        "signed_artifacts_pct": signed_pct,
        "signed_artifacts": signed,
        "blocked_deployments": blocked,
        "allowed_deployments": allowed,
        "integrity_failures": integrity_failures,
        "pipeline_runs": pipeline_runs,
        "failed_pipeline_runs": failed_runs,
        "avg_admission_latency_ms": round(avg_latency, 1),
    }


def serialize_artifact(row: ModelArtifact) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "image_ref": row.image_ref,
        "data_sha256": row.data_sha256,
        "model_sha256": row.model_sha256,
        "signature_digest": row.signature_digest,
        "signature_status": row.signature_status,
        "source": row.source,
        "updated_at": row.updated_at.isoformat(),
    }


def serialize_admission(row: AdmissionDecision) -> dict:
    return {
        "id": row.id,
        "artifact_id": row.artifact_id,
        "artifact_name": row.artifact.name,
        "artifact_version": row.artifact.version,
        "image_ref": row.artifact.image_ref,
        "result": row.result,
        "reason": row.reason,
        "verified_by": row.verified_by,
        "latency_ms": row.latency_ms,
        "created_at": row.created_at.isoformat(),
    }


def serialize_pipeline_run(row: PipelineRun) -> dict:
    return {
        "id": row.id,
        "artifact_id": row.artifact_id,
        "artifact_name": row.artifact.name,
        "artifact_version": row.artifact.version,
        "commit_sha": row.commit_sha,
        "workflow": row.workflow,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def list_artifacts(org, page: int = 1, page_size: int = 25) -> dict:
    qs = ModelArtifact.objects.filter(organization=org).order_by("-updated_at")
    page_obj, meta = _paginate(qs, page, page_size)
    return {
        **meta,
        "results": [serialize_artifact(row) for row in page_obj],
    }


def list_admission_log(org, period: str = "24h", page: int = 1, page_size: int = 25) -> dict:
    since = _since(period)
    qs = (
        AdmissionDecision.objects.filter(organization=org, created_at__gte=since)
        .select_related("artifact")
        .order_by("-created_at")
    )
    page_obj, meta = _paginate(qs, page, page_size)
    return {
        "period": period,
        **meta,
        "results": [serialize_admission(row) for row in page_obj],
    }


def list_pipeline_runs(org, period: str = "24h", page: int = 1, page_size: int = 25) -> dict:
    since = _since(period)
    qs = (
        PipelineRun.objects.filter(organization=org, started_at__gte=since)
        .select_related("artifact")
        .order_by("-started_at")
    )
    page_obj, meta = _paginate(qs, page, page_size)
    return {
        "period": period,
        **meta,
        "results": [serialize_pipeline_run(row) for row in page_obj],
    }


def build_k8s_firewall_summary(org, period: str = "24h") -> dict:
    since = _since(period)
    pods = WorkloadPod.objects.filter(organization=org)
    total_pods = pods.count()
    with_sidecar = pods.filter(sidecar_attached=True).count()
    sidecar_pct = round(with_sidecar / total_pods * 100, 1) if total_pods else 0.0

    mtls_healthy = pods.filter(mtls_status="healthy").count()
    mtls_pct = round(mtls_healthy / total_pods * 100, 1) if total_pods else 0.0

    drops = NetworkPolicyEvent.objects.filter(
        organization=org, action="drop", created_at__gte=since
    ).count()
    quarantined = EmbeddingInspectionJob.objects.filter(
        organization=org, status="quarantined", created_at__gte=since
    ).count()

    clusters = ClusterRegistration.objects.filter(organization=org).count()

    return {
        "period": period,
        "clusters": clusters,
        "total_pods": total_pods,
        "sidecar_coverage_pct": sidecar_pct,
        "pods_with_sidecar": with_sidecar,
        "mtls_healthy_pct": mtls_pct,
        "mtls_healthy_pods": mtls_healthy,
        "packets_dropped": drops,
        "quarantined_embeddings": quarantined,
    }


def build_topology(org) -> dict:
    clusters = ClusterRegistration.objects.filter(organization=org).prefetch_related("pods")
    result = []
    for cluster in clusters:
        ns_map: dict[str, list] = defaultdict(list)
        for pod in cluster.pods.all():
            ns_map[pod.namespace].append(
                {
                    "id": pod.id,
                    "pod_name": pod.pod_name,
                    "workload_type": pod.workload_type,
                    "sidecar_attached": pod.sidecar_attached,
                    "mtls_status": pod.mtls_status,
                    "labels": pod.labels or {},
                    "last_seen_at": pod.last_seen_at.isoformat() if pod.last_seen_at else None,
                }
            )
        result.append(
            {
                "id": cluster.id,
                "name": cluster.name,
                "k8s_version": cluster.k8s_version,
                "cilium_enabled": cluster.cilium_enabled,
                "status": cluster.status,
                "last_heartbeat_at": cluster.last_heartbeat_at.isoformat()
                if cluster.last_heartbeat_at
                else None,
                "namespaces": [
                    {"name": ns, "pods": pods_list} for ns, pods_list in sorted(ns_map.items())
                ],
            }
        )
    return {"clusters": result}


def serialize_network_event(row: NetworkPolicyEvent) -> dict:
    return {
        "id": row.id,
        "cluster_id": row.cluster_id,
        "cluster_name": row.cluster.name if row.cluster_id else "",
        "layer": row.layer,
        "action": row.action,
        "source_ref": row.source_ref,
        "dest_ref": row.dest_ref,
        "reason": row.reason,
        "created_at": row.created_at.isoformat(),
    }


def list_network_events(
    org,
    period: str = "24h",
    layer: str | None = None,
    action: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    since = _since(period)
    qs = (
        NetworkPolicyEvent.objects.filter(organization=org, created_at__gte=since)
        .select_related("cluster")
        .order_by("-created_at")
    )
    if layer:
        qs = qs.filter(layer=layer)
    if action:
        qs = qs.filter(action=action)
    page_obj, meta = _paginate(qs, page, page_size)
    return {
        "period": period,
        **meta,
        "results": [serialize_network_event(row) for row in page_obj],
    }


def serialize_embedding_job(row: EmbeddingInspectionJob) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "collection": row.collection,
        "anomaly_score": row.anomaly_score,
        "payload_hash": row.payload_hash,
        "quarantine_reason": row.quarantine_reason,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def list_embedding_queue(
    org,
    period: str = "24h",
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    since = _since(period)
    qs = EmbeddingInspectionJob.objects.filter(organization=org, created_at__gte=since).order_by(
        "-created_at"
    )
    if status:
        qs = qs.filter(status=status)
    page_obj, meta = _paginate(qs, page, page_size)
    return {
        "period": period,
        **meta,
        "results": [serialize_embedding_job(row) for row in page_obj],
    }

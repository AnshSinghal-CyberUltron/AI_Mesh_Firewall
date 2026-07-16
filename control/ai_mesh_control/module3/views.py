"""Module 3 API views — LLMOps pipeline security and K8s-native firewall."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from auth.utils import get_request_organization
from core.admin_views import IsAdminOrSuperuser
from core.agent_auth import AgentAPIKeyPermission, AgentKeyAuthentication
from module3 import analytics
from module3.adapters.gateway_admission import verify_admission
from module3.adapters.ingestion import normalize_pod_payload
from module3.models import (
    AdmissionDecision,
    ApiGovernanceEvent,
    ApiQuotaPolicy,
    ClusterRegistration,
    EmbeddingInspectionJob,
    ModelArtifact,
    NetworkPolicyEvent,
    WorkloadPod,
)
from module3.quota_ops import build_opa_quota_snapshot, ensure_usage, increment_usage
from module3.serializers import (
    ApiGovernanceEventSerializer,
    ApiQuotaPolicySerializer,
    ApiQuotaPolicyWriteSerializer,
    ClusterHeartbeatSerializer,
    EmbeddingInspectionIngestSerializer,
    GovernanceEventIngestSerializer,
    ModelArtifactCreateSerializer,
    ModelArtifactSerializer,
    NetworkEventIngestSerializer,
    QuotaUsageIngestSerializer,
    VerifyArtifactSerializer,
)
from module3.tasks import (
    emit_admission_deny_incident,
    emit_api_governance_deny_incident,
    emit_embedding_quarantine_incident,
    emit_network_drop_incident,
)

logger = logging.getLogger(__name__)


def _org_or_403(request):
    org = get_request_organization(request)
    if org is None and not getattr(request.user, "is_superuser", False):
        return None
    return org


def _page_params(request) -> tuple[int, int]:
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int(request.query_params.get("page_size", 25))))
    except (TypeError, ValueError):
        page_size = 25
    return page, page_size


def _period(request) -> str:
    return str(request.query_params.get("period") or "24h")


class LlmopsSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        return Response(analytics.build_llmops_summary(org, _period(request)))


class LlmopsArtifactsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        page, page_size = _page_params(request)
        return Response(analytics.list_artifacts(org, page, page_size))

    def post(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        if not IsAdminOrSuperuser().has_permission(request, self):
            return Response({"detail": "Admin required to register artifacts."}, status=status.HTTP_403_FORBIDDEN)
        ser = ModelArtifactCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        digest = data.get("signature_digest") or ""
        sig_status = "unsigned"
        if digest.startswith("invalid:") or digest == "invalid":
            sig_status = "invalid"
        elif digest:
            sig_status = "pending"
        artifact, _created = ModelArtifact.objects.update_or_create(
            organization=org,
            name=data["name"],
            version=data["version"],
            defaults={
                "image_ref": data["image_ref"],
                "data_sha256": data.get("data_sha256") or "",
                "model_sha256": data.get("model_sha256") or "",
                "signature_digest": digest,
                "signature_status": sig_status,
                "source": data.get("source") or "manual",
            },
        )
        return Response(ModelArtifactSerializer(artifact).data, status=status.HTTP_201_CREATED)


class LlmopsAdmissionLogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        page, page_size = _page_params(request)
        return Response(analytics.list_admission_log(org, _period(request), page, page_size))


class LlmopsPipelineRunsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        page, page_size = _page_params(request)
        return Response(analytics.list_pipeline_runs(org, _period(request), page, page_size))


class LlmopsVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)

        ser = VerifyArtifactSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        artifact = None
        if data.get("artifact_id"):
            artifact = ModelArtifact.objects.filter(pk=data["artifact_id"], organization=org).first()
            if not artifact:
                return Response({"detail": "Artifact not found."}, status=status.HTTP_404_NOT_FOUND)
        else:
            image_ref = (data.get("image_ref") or "").strip()
            if not image_ref:
                return Response({"detail": "artifact_id or image_ref required."}, status=status.HTTP_400_BAD_REQUEST)
            artifact = ModelArtifact.objects.filter(organization=org, image_ref=image_ref).first()
            if not artifact:
                artifact = ModelArtifact.objects.create(
                    organization=org,
                    name=image_ref.rsplit("/", 1)[-1].split(":")[0],
                    version="sim",
                    image_ref=image_ref,
                    data_sha256=data.get("data_sha256") or "",
                    model_sha256=data.get("model_sha256") or "",
                    signature_digest=data.get("signature_digest") or "",
                    signature_status="pending",
                    source="manual",
                )

        payload = {
            "image_ref": artifact.image_ref,
            "signature_digest": data.get("signature_digest") or artifact.signature_digest,
            "data_sha256": data.get("data_sha256") or artifact.data_sha256,
            "model_sha256": data.get("model_sha256") or artifact.model_sha256,
        }

        try:
            gw_result = verify_admission(payload)
        except RuntimeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        allowed = bool(gw_result.get("allowed"))
        result = "allow" if allowed else "deny"
        reason = str(gw_result.get("reason") or "")
        latency_ms = int(gw_result.get("latency_ms") or 0)

        artifact.signature_status = "valid" if allowed else "invalid"
        if payload.get("signature_digest"):
            artifact.signature_digest = payload["signature_digest"]
        artifact.save(update_fields=["signature_status", "signature_digest", "updated_at"])

        decision = AdmissionDecision.objects.create(
            organization=org,
            artifact=artifact,
            result=result,
            reason=reason,
            verified_by="gateway",
            latency_ms=latency_ms,
        )

        if not allowed:
            emit_admission_deny_incident.delay(org.id, artifact.id, reason)

        return Response(
            {
                "decision": analytics.serialize_admission(decision),
                "gateway": gw_result,
            },
            status=status.HTTP_200_OK,
        )


class K8sFirewallSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        return Response(analytics.build_k8s_firewall_summary(org, _period(request)))


class K8sFirewallTopologyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        return Response(analytics.build_topology(org))


class K8sFirewallNetworkEventsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        page, page_size = _page_params(request)
        layer = request.query_params.get("layer") or None
        action = request.query_params.get("action") or None
        return Response(
            analytics.list_network_events(org, _period(request), layer, action, page, page_size)
        )


class K8sFirewallEmbeddingQueueView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        page, page_size = _page_params(request)
        job_status = request.query_params.get("status") or None
        return Response(
            analytics.list_embedding_queue(org, _period(request), job_status, page, page_size)
        )


class _IngestOrgMixin:
    """Resolve organization from agent key or authenticated user."""

    def _resolve_ingest_org(self, request):
        org_id = getattr(request, "agent_organization_id", None)
        if org_id:
            from auth.models import Organization

            return Organization.objects.filter(pk=org_id, is_active=True).first()

        # Global AGENT_API_KEY has no org binding — accept explicit slug/id in body or query.
        from auth.models import Organization

        data = getattr(request, "data", None) or {}
        slug = str(
            (data.get("organization_slug") if hasattr(data, "get") else None)
            or request.query_params.get("organization_slug")
            or ""
        ).strip()
        if slug:
            return Organization.objects.filter(slug=slug, is_active=True).first()
        raw_pk = None
        if hasattr(data, "get"):
            raw_pk = data.get("organization_id")
        if raw_pk is None:
            raw_pk = request.query_params.get("organization_id")
        if raw_pk is not None and str(raw_pk).strip() != "":
            try:
                return Organization.objects.filter(pk=int(raw_pk), is_active=True).first()
            except (TypeError, ValueError):
                return None

        return get_request_organization(request)


class ClusterHeartbeatIngestView(_IngestOrgMixin, APIView):
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request):
        org = self._resolve_ingest_org(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)

        ser = ClusterHeartbeatSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        now = timezone.now()

        cluster, _ = ClusterRegistration.objects.update_or_create(
            organization=org,
            name=data["cluster_name"],
            defaults={
                "k8s_version": data.get("k8s_version") or "",
                "cilium_enabled": data.get("cilium_enabled", True),
                "status": data.get("status") or "healthy",
                "last_heartbeat_at": now,
            },
        )

        for raw_pod in data.get("pods") or []:
            pod_data = normalize_pod_payload(raw_pod)
            if not pod_data["pod_name"]:
                continue
            WorkloadPod.objects.update_or_create(
                organization=org,
                cluster=cluster,
                namespace=pod_data["namespace"],
                pod_name=pod_data["pod_name"],
                defaults={
                    "workload_type": pod_data["workload_type"],
                    "labels": pod_data["labels"],
                    "sidecar_attached": pod_data["sidecar_attached"],
                    "mtls_status": pod_data["mtls_status"],
                    "last_seen_at": now,
                },
            )

        return Response({"status": "ok", "cluster_id": cluster.id})


class NetworkEventIngestView(_IngestOrgMixin, APIView):
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request):
        org = self._resolve_ingest_org(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)

        ser = NetworkEventIngestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        cluster = None
        cluster_name = (data.get("cluster_name") or "").strip()
        if cluster_name:
            cluster, _ = ClusterRegistration.objects.get_or_create(
                organization=org,
                name=cluster_name,
                defaults={"k8s_version": "", "cilium_enabled": True, "status": "healthy"},
            )

        event = NetworkPolicyEvent.objects.create(
            organization=org,
            cluster=cluster,
            layer=data["layer"],
            action=data["action"],
            source_ref=data["source_ref"],
            dest_ref=data["dest_ref"],
            reason=data.get("reason") or "",
        )
        if data["action"] == "drop":
            emit_network_drop_incident.delay(org.id, event.id, event.reason)
        return Response({"status": "ok", "event_id": event.id}, status=status.HTTP_201_CREATED)


class EmbeddingInspectionIngestView(_IngestOrgMixin, APIView):
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request):
        org = self._resolve_ingest_org(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)

        ser = EmbeddingInspectionIngestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        job = EmbeddingInspectionJob.objects.create(
            organization=org,
            status=data["status"],
            collection=data.get("collection") or "",
            anomaly_score=float(data.get("anomaly_score") or 0.0),
            payload_hash=data.get("payload_hash") or "",
            quarantine_reason=data.get("quarantine_reason") or "",
        )

        if data["status"] == "quarantined":
            emit_embedding_quarantine_incident.delay(
                org.id,
                job.id,
                job.collection,
                job.quarantine_reason,
            )

        return Response(
            {"status": "ok", "job_id": job.id, "job_status": job.status},
            status=status.HTTP_201_CREATED,
        )


class Module3SimulatorIngestView(APIView):
    """Authenticated simulator endpoint for UI demos (JWT, not agent key)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)

        event_type = str(request.data.get("event_type") or "").strip()
        if event_type == "network_drop":
            payload = {
                "cluster_name": request.data.get("cluster_name") or "demo-cluster",
                "layer": request.data.get("layer") or "ebpf",
                "action": "drop",
                "source_ref": request.data.get("source_ref") or "compromised/pod",
                "dest_ref": request.data.get("dest_ref") or "vector-db/default",
                "reason": request.data.get("reason")
                or "Unauthorized label: missing python-backend",
            }
            ser = NetworkEventIngestSerializer(data=payload)
            ser.is_valid(raise_exception=True)
            data = ser.validated_data
            cluster_name = (data.get("cluster_name") or "demo-cluster").strip()
            cluster, _ = ClusterRegistration.objects.get_or_create(
                organization=org,
                name=cluster_name,
                defaults={"k8s_version": "1.28", "cilium_enabled": True, "status": "healthy"},
            )
            event = NetworkPolicyEvent.objects.create(
                organization=org,
                cluster=cluster,
                layer=data.get("layer") or "ebpf",
                action="drop",
                source_ref=data.get("source_ref") or "compromised/pod",
                dest_ref=data.get("dest_ref") or "vector-db/default",
                reason=data.get("reason") or "Unauthorized label: missing python-backend",
            )
            emit_network_drop_incident.delay(org.id, event.id, event.reason)
            return Response({"status": "ok", "event_id": event.id})

        if event_type == "embedding_poison":
            payload = {
                "collection": request.data.get("collection") or "corp-docs",
                "status": "quarantined",
                "anomaly_score": request.data.get("anomaly_score") or 0.97,
                "payload_hash": request.data.get("payload_hash") or "sim-poison-hash",
                "quarantine_reason": request.data.get("quarantine_reason")
                or "Extreme distance anomaly — suspected embedding poisoning",
            }
            ser = EmbeddingInspectionIngestSerializer(data=payload)
            ser.is_valid(raise_exception=True)
            data = ser.validated_data
            job = EmbeddingInspectionJob.objects.create(
                organization=org,
                status="quarantined",
                collection=data.get("collection") or "corp-docs",
                anomaly_score=float(data.get("anomaly_score") or 0.97),
                payload_hash=data.get("payload_hash") or "sim-poison-hash",
                quarantine_reason=data.get("quarantine_reason")
                or "Extreme distance anomaly — suspected embedding poisoning",
            )
            emit_embedding_quarantine_incident.delay(
                org.id, job.id, job.collection, job.quarantine_reason
            )
            return Response({"status": "ok", "job_id": job.id})

        return Response({"detail": "Unknown event_type."}, status=status.HTTP_400_BAD_REQUEST)


class ApiGovernanceSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        policies = ApiQuotaPolicy.objects.filter(organization=org)
        events = ApiGovernanceEvent.objects.filter(organization=org)
        since = timezone.now() - timedelta(hours=24)
        recent = events.filter(created_at__gte=since)
        return Response(
            {
                "policy_count": policies.count(),
                "enabled_policies": policies.filter(enabled=True).count(),
                "denials_24h": recent.filter(action="deny").count(),
                "allows_24h": recent.filter(action="allow").count(),
                "opa_synced": True,
            }
        )


class ApiGovernancePoliciesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        qs = ApiQuotaPolicy.objects.filter(organization=org).select_related("usage")
        for policy in qs:
            ensure_usage(policy)
        page, page_size = _page_params(request)
        total = qs.count()
        start = (page - 1) * page_size
        rows = qs[start : start + page_size]
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": ApiQuotaPolicySerializer(rows, many=True).data,
            }
        )

    def post(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        if not IsAdminOrSuperuser().has_permission(request, self):
            return Response({"detail": "Admin required."}, status=status.HTTP_403_FORBIDDEN)
        ser = ApiQuotaPolicyWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        policy, created = ApiQuotaPolicy.objects.update_or_create(
            organization=org,
            tenant_id=data["tenant_id"],
            environment=data["environment"],
            defaults={
                "tokens_per_minute": data["tokens_per_minute"],
                "tokens_per_day": data["tokens_per_day"],
                "enabled": data["enabled"],
                "denied_paths": data.get("denied_paths") or [],
            },
        )
        ensure_usage(policy)
        return Response(
            ApiQuotaPolicySerializer(policy).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ApiGovernancePolicyDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, policy_id: int):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        if not IsAdminOrSuperuser().has_permission(request, self):
            return Response({"detail": "Admin required."}, status=status.HTTP_403_FORBIDDEN)
        policy = ApiQuotaPolicy.objects.filter(organization=org, pk=policy_id).first()
        if policy is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        ser = ApiQuotaPolicyWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        for key in (
            "tenant_id",
            "environment",
            "tokens_per_minute",
            "tokens_per_day",
            "enabled",
            "denied_paths",
        ):
            if key in data:
                setattr(policy, key, data[key])
        policy.save()
        ensure_usage(policy)
        return Response(ApiQuotaPolicySerializer(policy).data)


class ApiGovernanceEventsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _org_or_403(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        qs = ApiGovernanceEvent.objects.filter(organization=org)
        action = (request.query_params.get("action") or "").strip()
        if action in ("allow", "deny"):
            qs = qs.filter(action=action)
        page, page_size = _page_params(request)
        total = qs.count()
        start = (page - 1) * page_size
        rows = qs[start : start + page_size]
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": ApiGovernanceEventSerializer(rows, many=True).data,
            }
        )


class QuotaSnapshotIngestView(_IngestOrgMixin, APIView):
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def get(self, request):
        org = self._resolve_ingest_org(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        return Response({"quotas": build_opa_quota_snapshot(org), "organization_slug": org.slug})


class GovernanceEventIngestView(_IngestOrgMixin, APIView):
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request):
        org = self._resolve_ingest_org(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        ser = GovernanceEventIngestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        event = ApiGovernanceEvent.objects.create(
            organization=org,
            action=data["action"],
            tenant_id=data.get("tenant_id") or "",
            environment=data.get("environment") or "",
            estimated_tokens=int(data.get("estimated_tokens") or 0),
            path=data.get("path") or "",
            reason=data.get("reason") or "",
            source=data.get("source") or "envoy_ext_authz",
        )
        if data["action"] == "deny":
            emit_api_governance_deny_incident.delay(org.id, event.id, event.reason)
        return Response(
            {"status": "ok", "event_id": event.id},
            status=status.HTTP_201_CREATED,
        )


class QuotaUsageIngestView(_IngestOrgMixin, APIView):
    authentication_classes = [AgentKeyAuthentication]
    permission_classes = [AgentAPIKeyPermission]

    def post(self, request):
        org = self._resolve_ingest_org(request)
        if org is None:
            return Response({"detail": "Organization required."}, status=status.HTTP_403_FORBIDDEN)
        ser = QuotaUsageIngestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        policy = ApiQuotaPolicy.objects.filter(
            organization=org,
            tenant_id=data["tenant_id"],
            environment=data["environment"],
        ).first()
        if policy is None:
            return Response({"detail": "Policy not found."}, status=status.HTTP_404_NOT_FOUND)
        usage = increment_usage(policy, int(data["tokens"]))
        return Response(
            {
                "status": "ok",
                "policy_id": policy.id,
                "tokens_minute": usage.tokens_minute,
                "tokens_day": usage.tokens_day,
            }
        )

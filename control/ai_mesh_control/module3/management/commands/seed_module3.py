"""Seed demo data for Module 3 pages."""

from datetime import timedelta
import hashlib

from django.core.management.base import BaseCommand
from django.utils import timezone

from auth.models import Organization
from module3.models import (
    AdmissionDecision,
    ClusterRegistration,
    EmbeddingInspectionJob,
    ModelArtifact,
    NetworkPolicyEvent,
    PipelineRun,
    WorkloadPod,
)


class Command(BaseCommand):
    help = "Seed Module 3 demo data (LLMOps artifacts, admission log, K8s topology)."

    def add_arguments(self, parser):
        parser.add_argument("--org-slug", default="", help="Organization slug (default: first active org)")

    def handle(self, *args, **options):
        slug = (options.get("org_slug") or "").strip()
        if slug:
            org = Organization.objects.filter(slug=slug, is_active=True).first()
        else:
            org = Organization.objects.filter(is_active=True).first()
        if not org:
            self.stderr.write("No active organization found.")
            return

        now = timezone.now()

        artifacts_spec = [
            ("zeroshield-rag-model", "1.4.2", "registry.zeroshield.ai/models/rag:1.4.2", "valid", "sha256:abc111"),
            ("zeroshield-chat-model", "2.0.0", "registry.zeroshield.ai/models/chat:2.0.0", "valid", "sha256:abc222"),
            ("legacy-embedder", "0.9.1", "registry.zeroshield.ai/models/embed:0.9.1", "pending", "sha256:pending"),
            ("unverified-experimental", "0.1.0", "registry.zeroshield.ai/models/exp:0.1.0", "invalid", "invalid:unsigned"),
            ("corp-finetune", "3.1.0", "registry.zeroshield.ai/models/finetune:3.1.0", "valid", "sha256:abc333"),
        ]

        artifacts = []
        for name, version, image_ref, sig_status, digest in artifacts_spec:
            data_hash = hashlib.sha256(f"{name}-data".encode()).hexdigest()
            model_hash = hashlib.sha256(f"{name}-model".encode()).hexdigest()
            art, _ = ModelArtifact.objects.update_or_create(
                organization=org,
                name=name,
                version=version,
                defaults={
                    "image_ref": image_ref,
                    "data_sha256": data_hash,
                    "model_sha256": model_hash,
                    "signature_digest": digest,
                    "signature_status": sig_status,
                    "source": "ci",
                },
            )
            artifacts.append(art)

        for i, art in enumerate(artifacts[:3]):
            PipelineRun.objects.get_or_create(
                organization=org,
                artifact=art,
                commit_sha=f"deadbeef{i:04d}",
                workflow="build-and-sign",
                defaults={
                    "status": "success" if art.signature_status == "valid" else "failed",
                    "started_at": now - timedelta(hours=6 - i),
                    "finished_at": now - timedelta(hours=5 - i),
                },
            )

        for art in artifacts:
            if art.signature_status == "valid":
                AdmissionDecision.objects.get_or_create(
                    organization=org,
                    artifact=art,
                    result="allow",
                    defaults={
                        "reason": "Cosign signature verified",
                        "latency_ms": 42,
                    },
                )
            elif art.signature_status == "invalid":
                AdmissionDecision.objects.get_or_create(
                    organization=org,
                    artifact=art,
                    result="deny",
                    defaults={
                        "reason": "Missing or invalid Cosign signature",
                        "latency_ms": 18,
                    },
                )

        cluster, _ = ClusterRegistration.objects.update_or_create(
            organization=org,
            name="prod-ai-cluster",
            defaults={
                "k8s_version": "1.28",
                "cilium_enabled": True,
                "status": "healthy",
                "last_heartbeat_at": now,
            },
        )

        pods_spec = [
            ("ai-models", "rag-inference-0", "model", True, "healthy"),
            ("ai-models", "chat-gateway-0", "model", True, "healthy"),
            ("vector-db", "chroma-0", "vector_db", True, "healthy"),
            ("vector-db", "pinecone-proxy-0", "vector_db", True, "degraded"),
            ("agents", "mcp-runtime-0", "agent", True, "healthy"),
            ("agents", "legacy-agent-0", "agent", False, "missing"),
        ]
        for ns, pod_name, wtype, sidecar, mtls in pods_spec:
            WorkloadPod.objects.update_or_create(
                organization=org,
                cluster=cluster,
                namespace=ns,
                pod_name=pod_name,
                defaults={
                    "workload_type": wtype,
                    "labels": {"app": pod_name, "tier": wtype},
                    "sidecar_attached": sidecar,
                    "mtls_status": mtls,
                    "last_seen_at": now,
                },
            )

        for i in range(8):
            NetworkPolicyEvent.objects.get_or_create(
                organization=org,
                cluster=cluster,
                layer="ebpf" if i % 2 == 0 else "envoy",
                action="drop" if i < 5 else "allow",
                source_ref=f"compromised/worker-{i % 3}",
                dest_ref="vector-db/chroma-0",
                reason="Cilium policy: unauthorized sender label" if i < 5 else "mTLS identity verified",
                defaults={"created_at": now - timedelta(minutes=30 - i * 3)},
            )

        for i, status in enumerate(["clean", "clean", "quarantined", "queued", "clean"]):
            EmbeddingInspectionJob.objects.get_or_create(
                organization=org,
                collection=f"collection-{i}",
                payload_hash=hashlib.sha256(f"emb-{i}".encode()).hexdigest(),
                defaults={
                    "status": status,
                    "anomaly_score": 0.12 if status == "clean" else 0.94,
                    "quarantine_reason": "Embedding poisoning detected" if status == "quarantined" else "",
                    "created_at": now - timedelta(hours=i),
                },
            )

        self.stdout.write(self.style.SUCCESS(f"Seeded Module 3 data for org '{org.slug}' (id={org.id})"))

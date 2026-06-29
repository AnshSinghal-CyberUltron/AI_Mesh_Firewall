"""Normalize webhook payloads for Module 3 ingestion."""

from __future__ import annotations


def normalize_pod_payload(pod: dict) -> dict:
    return {
        "namespace": str(pod.get("namespace") or "default").strip(),
        "pod_name": str(pod.get("pod_name") or pod.get("name") or "").strip(),
        "workload_type": str(pod.get("workload_type") or "model").strip(),
        "labels": pod.get("labels") if isinstance(pod.get("labels"), dict) else {},
        "sidecar_attached": bool(pod.get("sidecar_attached", False)),
        "mtls_status": str(pod.get("mtls_status") or "missing").strip(),
    }

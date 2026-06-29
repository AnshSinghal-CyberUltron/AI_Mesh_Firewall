/** KPI builders for Module 3 pages. */

export function buildLlmopsKpis(summary = {}) {
  return [
    {
      key: "signed-pct",
      label: "Signed Artifacts",
      value: `${summary.signed_artifacts_pct ?? 0}%`,
      helpText: "Share of registered artifacts with valid Cosign signatures.",
      color: (summary.signed_artifacts_pct ?? 0) >= 80 ? "text-emerald-600" : "text-amber-600",
    },
    {
      key: "blocked",
      label: "Blocked Deployments",
      value: summary.blocked_deployments ?? 0,
      helpText: "Admission gatekeeper denials in this time window.",
      color: (summary.blocked_deployments ?? 0) > 0 ? "text-red-600" : undefined,
    },
    {
      key: "integrity",
      label: "Integrity Failures",
      value: summary.integrity_failures ?? 0,
      helpText: "Artifacts with invalid signatures or missing SHA-256 fingerprints.",
      color: (summary.integrity_failures ?? 0) > 0 ? "text-amber-600" : undefined,
    },
    {
      key: "pipeline-runs",
      label: "Pipeline Runs",
      value: summary.pipeline_runs ?? 0,
      helpText: "CI/CD build-and-sign runs in this window.",
    },
    {
      key: "latency",
      label: "Avg Admission (ms)",
      value: summary.avg_admission_latency_ms ?? 0,
      helpText: "Mean gateway admission verification latency.",
    },
  ];
}

export function buildK8sFirewallKpis(summary = {}) {
  return [
    {
      key: "sidecar",
      label: "Sidecar Coverage",
      value: `${summary.sidecar_coverage_pct ?? 0}%`,
      helpText: "Pods with Envoy sidecar attached for L7 inspection.",
      color: (summary.sidecar_coverage_pct ?? 0) >= 90 ? "text-emerald-600" : "text-amber-600",
    },
    {
      key: "mtls",
      label: "mTLS Healthy",
      value: `${summary.mtls_healthy_pct ?? 0}%`,
      helpText: "Workloads with healthy mutual TLS identity.",
      color: (summary.mtls_healthy_pct ?? 0) >= 90 ? "text-emerald-600" : "text-amber-600",
    },
    {
      key: "drops",
      label: "Packets Dropped",
      value: summary.packets_dropped ?? 0,
      helpText: "eBPF/Cilium and Envoy drop events in this window.",
      color: (summary.packets_dropped ?? 0) > 0 ? "text-red-600" : undefined,
    },
    {
      key: "quarantine",
      label: "Quarantined Embeddings",
      value: summary.quarantined_embeddings ?? 0,
      helpText: "Embedding inspection jobs flagged as poisoned.",
      color: (summary.quarantined_embeddings ?? 0) > 0 ? "text-red-600" : undefined,
    },
    {
      key: "pods",
      label: "Monitored Pods",
      value: summary.total_pods ?? 0,
      helpText: "AI workloads registered across all clusters.",
    },
  ];
}

export const PIPELINE_STAGES = [
  { id: "push", label: "Push", description: "Code and weights pushed to repository" },
  { id: "sha256", label: "SHA-256", description: "DVC integrity fingerprint check" },
  { id: "sign", label: "Build & Sign", description: "Docker build + Cosign signature" },
  { id: "deploy", label: "Deploy Request", description: "Kubernetes pulls container" },
  { id: "admission", label: "Admission", description: "FastAPI gatekeeper verify" },
];

export function signatureStatusBadge(status) {
  const map = {
    valid: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    invalid: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
    pending: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
    unsigned: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  };
  return map[status] || map.unsigned;
}

export function admissionResultBadge(result) {
  return result === "allow"
    ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
    : "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300";
}

export function mtlsBadge(status) {
  const map = {
    healthy: "bg-emerald-100 text-emerald-800",
    degraded: "bg-amber-100 text-amber-800",
    missing: "bg-red-100 text-red-800",
  };
  return map[status] || map.missing;
}

export function embeddingStatusBadge(status) {
  const map = {
    clean: "bg-emerald-100 text-emerald-800",
    quarantined: "bg-red-100 text-red-800",
    queued: "bg-slate-100 text-slate-700",
  };
  return map[status] || map.queued;
}

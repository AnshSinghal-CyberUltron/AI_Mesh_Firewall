/** Analyst-facing microcopy for Module 3 pages. */

export const INFRA_BRIEF_TITLE = "Page Objective";

export const PAGE_BRIEFS = {
  llmops:
    "Secure the model supply chain from training data to production deployment. Register Cosign-signed artifacts and run live gateway admission. Production uses MODULE3_ADMISSION_MODE=verify; local demos can use passthrough + Admission Simulator.",
  k8sFirewall:
    "Zero-trust visibility for AI workloads and Vector DBs. Phase 2 Kind/Helm agents post Cilium drops, Envoy mTLS posture, and River embedding inspection into Module 3 ingest; threats open Module 2 SOC incidents.",
  apiGovernance:
    "Network-edge kill-switch for LLM token quotas. Kind Envoy ext_authz + OPA Rego enforce per-tenant/environment budgets; denies post into Module 3 and open Module 2 SOC incidents.",
};

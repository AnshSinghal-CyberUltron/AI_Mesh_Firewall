// Single source of truth for §1.7 Generator-Level Output Guardrail visuals.
// Reused by the engine card, the recharts analytics lane, the governance log,
// and action badges so block/redact/rewrite/flag/allow read identically
// everywhere (resolves the prior block/allowed vs rewritten enum drift, and the
// grey fallback the backend ModuleChartsView emits for rewrite/flag).

export const OUTPUT_ACTION_COLORS = {
  block: "#ef4444", // red-500
  redact: "#f59e0b", // amber-500
  rewrite: "#8b5cf6", // violet-500
  flag: "#fb923c", // orange-400
  allow: "#10b981", // emerald-500
  monitor: "#10b981", // monitor ≈ allow (observe only)
  alert: "#fb923c",
  allowed: "#10b981",
  blocked: "#ef4444",
  redacted: "#f59e0b",
  monitored: "#10b981",
};

export const OUTPUT_ACTION_FALLBACK = "#64748b"; // slate-500

export function actionColor(action) {
  return OUTPUT_ACTION_COLORS[String(action || "").toLowerCase()] || OUTPUT_ACTION_FALLBACK;
}

// Canonical engine-card detection categories.
export const OUTPUT_CATEGORIES = ["pii", "credential", "hallucination", "ip_leakage", "secret", "data_leakage"];

// Map the gateway's threat_type / threat_category vocabulary onto the six
// canonical categories. Anything unmapped rolls up to "other" (never dropped).
const CATEGORY_NORMALIZE = {
  pii: "pii", pii_exposure: "pii", pd: "pii", "pii/pd": "pii", personal_data: "pii",
  credential: "credential", credential_exposure: "credential", credentials: "credential", api_key: "credential", token: "credential",
  hallucination: "hallucination", hallucination_risk: "hallucination", hallucination_score: "hallucination", factuality: "hallucination",
  ip_leakage: "ip_leakage", ip_leak: "ip_leakage", ip: "ip_leakage", intellectual_property: "ip_leakage",
  secret: "secret", secrets: "secret", secret_detection: "secret",
  data_leakage: "data_leakage", data_exfiltration: "data_leakage", data_leak: "data_leakage", exfiltration: "data_leakage",
};

export function normalizeCategory(raw) {
  const key = String(raw || "").toLowerCase().trim().replace(/\s+/g, "_");
  return CATEGORY_NORMALIZE[key] || "other";
}

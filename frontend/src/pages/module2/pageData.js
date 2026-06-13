/** Pure data formatters for Module 2 page charts and tables. */

const EXPOSURE_COLORS = {
  high: "#ef4444",
  medium: "#f59e0b",
  low: "#10b981",
};

const TELEMETRY_COLORS = {
  injection_attempts: "#ef4444",
  pii_leaks: "#f59e0b",
  behavior_scoring: "#0ea5e9",
  threat_intel_matches: "#8b5cf6",
};

export function formatExposureChartData(exposureByModel = []) {
  return exposureByModel.map((row) => ({
    name: row.model,
    score: row.exposure_score,
    blockRate: row.block_rate_pct,
    fill: EXPOSURE_COLORS[row.exposure_band] || "#64748b",
  }));
}

export function formatTelemetryTimeline(timeline = []) {
  return timeline.map((point) => ({
    ...point,
    label: point.timestamp?.slice(5, 16)?.replace("T", " ") || "",
  }));
}

export function formatAttackVectors(vectors = []) {
  return vectors.map((row) => ({
    name: row.vector,
    count: row.count,
  }));
}

export function buildExposureKpis(summary = {}) {
  return [
    { key: "active-models", label: "Active Models", value: summary.active_models ?? 0, helpText: "Distinct LLM targets observed in enforcement telemetry for this period." },
    { key: "high-exposure", label: "High Exposure", value: summary.high_exposure_models ?? 0, color: "text-red-600", helpText: "Models in the high exposure band—prioritize for policy review or routing changes." },
    { key: "total-requests", label: "Total Requests", value: summary.total_requests ?? 0, helpText: "Aggregate request volume across all monitored models." },
    { key: "avg-block-rate", label: "Avg Block Rate", value: `${summary.avg_block_rate_pct ?? 0}%`, helpText: "Fleet-wide mean block rate; sudden lifts may signal active attack campaigns." },
    { key: "avg-exposure", label: "Avg Exposure", value: summary.avg_exposure_score ?? 0, helpText: "Mean composite exposure score (0–1). Higher values indicate elevated enforcement pressure." },
  ];
}

export function buildTelemetryKpis(summary = {}) {
  return [
    { key: "total-events", label: "Total Events", value: summary.total_events ?? 0, helpText: "All categorized enforcement events in the selected analysis window." },
    { key: "injection-attempts", label: "Injection Attempts", value: summary.injection_attempts ?? 0, color: "text-red-600", helpText: "Prompt injection, jailbreak, or LLM01/LLM02-class attack signals." },
    { key: "pii-leaks", label: "PII Leaks", value: summary.pii_leaks ?? 0, color: "text-amber-600", helpText: "Events where sensitive data was detected and redacted or blocked." },
    { key: "behavior-scoring", label: "Behavior Scoring", value: summary.behavior_scoring_events ?? 0, color: "text-sky-600", helpText: "Events contributing to API-key UEBA risk scoring." },
    { key: "threat-intel-hits", label: "Threat Intel Hits", value: summary.threat_intel_matches ?? 0, color: "text-violet-600", helpText: "Enforcement events that matched a configured IOC or threat signature." },
  ];
}

export function telemetrySeriesKeys() {
  return Object.keys(TELEMETRY_COLORS);
}

export function telemetrySeriesColor(key) {
  return TELEMETRY_COLORS[key] || "#64748b";
}

export function exposureBandClass(band) {
  if (band === "high") return "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";
  if (band === "medium") return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
  return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
}

export function sourceBadgeClass(source) {
  if (source === "threat_intel") return "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300";
  if (source === "ueba") return "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300";
  if (source === "rag") return "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300";
  if (source === "mcp") return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
  if (source === "vector") return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
  if (source === "chat") return "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300";
  return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
}

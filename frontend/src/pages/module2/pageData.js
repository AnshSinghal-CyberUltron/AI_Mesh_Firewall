/** Pure data formatters for Module 2 page charts and tables. */

import { formatRiskBandLabel } from "../../utils/riskLabels.js";

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

export function formatTelemetryTimeline(timeline) {
  const points = Array.isArray(timeline) ? timeline : [];
  return points.map((point) => {
    const ts = point?.timestamp;
    const label =
      typeof ts === "string"
        ? ts.slice(5, 16).replace("T", " ")
        : ts != null
          ? String(ts).slice(0, 16)
          : "";
    return { ...point, label };
  });
}

export function formatAttackVectors(vectors) {
  const rows = Array.isArray(vectors) ? vectors : [];
  return rows.map((row) => ({
    name: row?.vector ?? "unknown",
    count: Number(row?.count) || 0,
  }));
}

const RAG_STAGE_LABELS = {
  query: "Query",
  retriever: "Retriever",
  ranker: "Ranker",
  generator: "Generator",
};

export function buildRagKpis(ragPipelineKpis = {}, vectorExposure = {}) {
  const stages = ragPipelineKpis?.stages || {};
  const stageList = Object.values(stages);
  const query = stages.query || {};
  const retriever = stages.retriever || {};
  const pipelineIngress = query.total || retriever.total || 0;
  const totalStageChecks = stageList.reduce((sum, st) => sum + (st?.total || 0), 0);
  const totalBlocked = stageList.reduce((sum, st) => sum + (st?.blocked || 0), 0);
  const collections = vectorExposure?.collections || [];
  const hotCollections = collections.filter((c) => (c.block_rate_pct ?? 0) >= 50).length;
  const retrieverTotal = retriever.total || 0;
  const retrieverBlocked = retriever.blocked || 0;
  const passRate = retrieverTotal
    ? Math.round(((retrieverTotal - retrieverBlocked) / retrieverTotal) * 100)
    : 100;

  return [
    {
      key: "pipeline-events",
      label: "Pipeline Events",
      value: pipelineIngress || totalStageChecks,
      helpText: "RAG requests entering the pipeline (Query stage volume when present).",
    },
    {
      key: "blocked-at-gate",
      label: "Blocked at Gate",
      value: totalBlocked,
      color: totalBlocked > 0 ? "text-red-600" : undefined,
      helpText: "Hard blocks recorded at any pipeline stage in this window.",
    },
    {
      key: "collections",
      label: "Collections",
      value: collections.length,
      helpText: "Distinct vector DB collections/namespaces touched during retrieval in this window.",
    },
    {
      key: "hot-collections",
      label: "High-Risk Collections",
      value: hotCollections,
      color: hotCollections > 0 ? "text-amber-600" : undefined,
      helpText: "Collections with block rate ≥ 50% — may indicate poisoned chunks or ACL issues.",
    },
    {
      key: "pass-rate",
      label: "Retriever Pass Rate",
      value: `${passRate}%`,
      color: passRate >= 80 ? "text-emerald-600" : passRate >= 50 ? "text-amber-600" : "text-red-600",
      helpText: "Share of Retriever stage checks that were not hard-blocked.",
    },
  ];
}

export function formatRagStageChartData(stages = {}) {
  return ["query", "retriever", "ranker", "generator"].map((key) => {
    const st = stages[key] || {};
    const total = st.total || 0;
    const blocked = st.blocked || 0;
    const flagged = st.flagged || 0;
    const allowed = st.allowed ?? Math.max(0, total - blocked - flagged - (st.rewritten || 0));
    return {
      stage: RAG_STAGE_LABELS[key] || key,
      stageKey: key,
      total,
      blocked,
      flagged,
      allowed,
      block_rate: total ? Math.round((blocked / total) * 100) : 0,
      avg_latency_ms: st.avg_latency_ms || 0,
    };
  });
}

export function formatRagDocumentFunnel(funnel = {}) {
  const retrieved = funnel.retrieved ?? 0;
  const postRanker = funnel.post_ranker ?? 0;
  const postGenerator = funnel.post_generator ?? 0;
  return [
    { step: "Retrieved", value: retrieved, pct: 100 },
    {
      step: "Survived Ranker",
      value: postRanker,
      pct: retrieved ? Math.round((postRanker / retrieved) * 100) : 0,
    },
    {
      step: "Survived Generator",
      value: postGenerator,
      pct: retrieved ? Math.round((postGenerator / retrieved) * 100) : 0,
    },
  ];
}

export function formatRagCollectionChartData(collections = []) {
  return collections.slice(0, 8).map((row) => ({
    name: row.collection,
    blockRate: row.block_rate_pct ?? 0,
    total: row.total ?? 0,
    blocked: row.blocked ?? 0,
  }));
}

export function formatRagEscalationChartData(escalation = {}) {
  return [
    { level: "Normal", count: escalation.normal ?? 0, fill: "#10b981" },
    { level: "Elevated", count: escalation.elevated ?? 0, fill: "#f59e0b" },
    { level: "Strict", count: escalation.strict ?? 0, fill: "#ef4444" },
  ];
}

export function buildUebaKpiItems({
  summary = {},
  containment = {},
  periodLabel = "selected window",
  containmentPanel = null,
  onDisabledClick,
  onKillSwitchClick,
}) {
  const s = summary;
  return [
    {
      key: "total-keys",
      label: "Total Keys",
      value: s.total_keys ?? 0,
      sub: "Registered fleet",
      helpText: "All API keys provisioned for this organization (not filtered by time window).",
    },
    {
      key: "active-keys",
      label: "Active Keys",
      value: s.active_keys ?? 0,
      color: "text-teal-600",
      sub: "Currently enabled",
      helpText: "Keys currently enabled and able to pass ingress auth (fleet snapshot).",
    },
    ...buildContainmentKpiItems({
      disabledKeys: s.disabled_keys ?? containment.disabled_keys ?? 0,
      activeKillSwitches: s.active_kill_switches ?? containment.active_kill_switches ?? 0,
      clickable: true,
      activePanel: containmentPanel,
      onDisabledClick,
      onKillSwitchClick,
    }),
    {
      key: "total-events",
      label: "Key Events",
      value: s.total_events ?? 0,
      sub: `Last ${periodLabel}`,
      helpText: "Enforcement events attributed to API keys in the selected time window.",
    },
    {
      key: "blocked-events",
      label: "Blocked",
      value: s.blocked_events ?? 0,
      color: "text-red-600",
      sub: `Last ${periodLabel}`,
      helpText: "Hard-blocked requests from API keys in the selected time window.",
    },
    {
      key: "keys-with-activity",
      label: "Keys With Activity",
      value: s.keys_with_activity ?? 0,
      sub: `Last ${periodLabel}`,
      helpText: "Distinct API keys with at least one enforcement event in the selected window.",
    },
    {
      key: "high-risk-keys",
      label: "High behavioral risk keys",
      value: s.high_risk_keys ?? 0,
      color: "text-red-600",
      sub: `Active in ${periodLabel}`,
      helpText: "Keys with activity in the window that score in the high UEBA risk band.",
    },
  ];
}

export function buildContainmentKpiItems({
  disabledKeys = 0,
  activeKillSwitches = 0,
  clickable = false,
  onDisabledClick,
  onKillSwitchClick,
  activePanel = null,
}) {
  return [
    {
      key: "disabled-keys",
      label: "Disabled Keys",
      value: disabledKeys,
      color: disabledKeys > 0 ? "text-orange-600" : undefined,
      helpText: "API credentials disabled at the gateway — all requests with these keys fail authentication.",
      sub: "Current state",
      clickable,
      onClick: onDisabledClick,
      active: activePanel === "disabled",
    },
    {
      key: "active-kill-switches",
      label: "Active Kill Switches",
      value: activeKillSwitches,
      color: activeKillSwitches > 0 ? "text-red-600" : undefined,
      helpText: "Credential- or model-scoped kill switches currently blocking traffic at the gateway.",
      sub: "Current state",
      clickable,
      onClick: onKillSwitchClick,
      active: activePanel === "kill-switch",
    },
  ];
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

export function formatConfidencePercent(confidence) {
  const c = Number(confidence) || 0;
  return c <= 1 ? `${Math.round(c * 100)}%` : `${Math.round(c)}%`;
}

/** Normalize list / paginated threat-intel API payloads to a row array. */
export function normalizeThreatIntelRows(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.results)) return data.results;
  return [];
}

/** Fleet stats for the IOC library table — used on M2.5 SOC panels. */
export function buildIocFleetStats(entries = []) {
  const list = Array.isArray(entries) ? entries : [];
  const now = Date.now();
  const weekMs = 7 * 24 * 60 * 60 * 1000;
  let autoBlock = 0;
  let expired = 0;
  let expiringSoon = 0;
  const bySource = { manual: 0, feed: 0, auto: 0 };

  for (const entry of list) {
    if (entry.auto_block) autoBlock += 1;
    const src = entry.source || "manual";
    if (Object.prototype.hasOwnProperty.call(bySource, src)) {
      bySource[src] += 1;
    } else {
      bySource.manual += 1;
    }
    if (entry.expires_at) {
      const expiresAt = new Date(entry.expires_at).getTime();
      if (expiresAt < now) expired += 1;
      else if (expiresAt - now < weekMs) expiringSoon += 1;
    }
  }

  return { total: list.length, autoBlock, expired, expiringSoon, bySource };
}

export function iocMatchRatePct(summary = {}) {
  const total = summary.total_events ?? 0;
  const hits = summary.threat_intel_matches ?? 0;
  if (!total) return 0;
  return Math.min(100, Math.round((hits / total) * 100));
}

export function buildTelemetryKpis(summary = {}) {
  return [
    {
      key: "total-events",
      label: "Total Events",
      value: summary.total_events ?? 0,
      helpText: "Every enforcement event in this time window (blocks, redactions, monitors). Broader than the category KPIs below.",
    },
    {
      key: "injection-attempts",
      label: "Injection & Jailbreak",
      value: summary.injection_attempts ?? 0,
      color: "text-red-600",
      helpText: "Prompt injection, jailbreak, or LLM01-style attacks — including Attack Simulator blocks.",
    },
    {
      key: "pii-leaks",
      label: "PII Detected",
      value: summary.pii_leaks ?? 0,
      color: "text-amber-600",
      helpText: "Requests where sensitive data was redacted by policy (SSN, PHI, credentials, etc.).",
    },
    {
      key: "behavior-scoring",
      label: "API Key Activity",
      value: summary.behavior_scoring_events ?? 0,
      color: "text-sky-600",
      helpText: "Events tied to a gateway API key prefix — feeds M2.2 UEBA; shown here for attack-volume context.",
    },
    {
      key: "threat-intel-hits",
      label: "IOC Matches",
      value: summary.threat_intel_matches ?? 0,
      color: "text-violet-600",
      helpText: "Traffic that matched an indicator from your IOC table after Sync to Gateway. Not the same as generic injection blocks.",
    },
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

const RISK_BAND_ORDER = ["low", "medium", "high"];

/** Pie chart rows for UEBA key risk bands — always includes all three tiers. */
export function formatRiskDistributionChart(distribution = {}) {
  return RISK_BAND_ORDER.map((band) => ({
    name: formatRiskBandLabel("behavioral", band),
    band,
    value: distribution[band] ?? 0,
  })).filter((row) => row.value > 0);
}

/** Derive enforcement lane (chat/rag/vector/mcp/ueba/threat_intel) from WS or incident payloads. */
export function resolveEventLane(item = {}) {
  if (item.source && item.source !== "policy" && item.source !== "gateway") {
    return item.source;
  }
  const meta = item.metadata || {};
  const eventType = String(meta.event_type || "").toLowerCase();
  if (eventType === "mcp_tool_call" || meta.tools_invoked || meta.mcp_server || meta.server_slug) {
    return "mcp";
  }
  if (eventType === "rag_pipeline") return "rag";
  if (meta.collection || meta.vector_collection || meta.vector_namespace) return "vector";
  const detail = String(meta.detail || "").toLowerCase();
  const src = String(meta.source || item.source || "").toLowerCase();
  const threatType = String(meta.threat_type || "").toLowerCase();
  if (detail.includes("threat intel") || src.includes("threat_intel") || threatType.startsWith("threat_intel")) {
    return "threat_intel";
  }
  if (meta.key_prefix || meta.api_key_prefix) return "ueba";
  return "chat";
}

export function formatTickerHeadline(item = {}) {
  if (item.title) return item.title;
  const meta = item.metadata || {};
  const parts = [];
  if (item.action) parts.push(String(item.action).toUpperCase());
  if (meta.threat_type) parts.push(meta.threat_type.replace(/_/g, " "));
  if (meta.model) parts.push(meta.model);
  const prefix = meta.key_prefix || meta.api_key_prefix;
  if (prefix) parts.push(prefix);
  if (meta.pipeline_stage) parts.push(`stage:${meta.pipeline_stage}`);
  return parts.length ? parts.join(" · ") : "Enforcement event";
}

export function formatTickerDetail(item = {}) {
  if (item.status && item.severity) return `${item.severity} · ${item.status}`;
  if (item.severity) return String(item.severity);
  if (item.category) {
    const sub = item.subcategory ? ` (${item.subcategory})` : "";
    return `${item.category}${sub}`;
  }
  if (item.message) return item.message;
  if (item.timestamp) return item.timestamp.replace("T", " ").slice(0, 19);
  return "";
}

/** Merge live WS feed with open incidents — dedupe by event/incident id. */
export function mergeTickerFeed(liveFeed = [], incidents = [], limit = 12) {
  const seen = new Set();
  const merged = [];
  const push = (item, fromFeed) => {
    const lane = resolveEventLane(item);
    const key = item.id
      ? (fromFeed ? `ev-${item.id}` : `inc-${item.id}`)
      : `live-${item.timestamp || ""}-${lane}-${formatTickerHeadline(item)}`;
    if (seen.has(key)) return;
    seen.add(key);
    merged.push({ ...item, _fromFeed: fromFeed });
  };
  liveFeed.forEach((item) => push(item, true));
  incidents.forEach((item) => push(item, false));
  return merged.slice(0, limit);
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

/** Read human-readable detail from enforcement metadata (top-level or nested extra). */
export function metadataDetail(meta = {}) {
  if (!meta || typeof meta !== "object") return "";
  if (meta.detail) return String(meta.detail);
  const extra = meta.extra;
  if (extra && typeof extra === "object" && extra.detail) return String(extra.detail);
  return "";
}

const INCIDENT_LANE_DRILL_DOWN = {
  chat: { to: "/models/exposure", label: "M2.3 Model exposure" },
  rag: { to: "/models/exposure?tab=rag", label: "M2.3 RAG health" },
  vector: { to: "/models/exposure?tab=rag", label: "M2.3 Vectors" },
  mcp: { to: "/mcp/risk", label: "M2.4 MCP risk" },
  ueba: { to: "/ueba/api-keys", label: "M2.2 UEBA" },
  threat_intel: { to: "/threat-intel", label: "M2.5 Threat intel" },
};

export function incidentLaneDrillDown(source) {
  return INCIDENT_LANE_DRILL_DOWN[source] || null;
}

const INCIDENT_SOURCE_ORDER = ["chat", "rag", "vector", "mcp", "ueba", "threat_intel", "generic"];

export function formatIncidentsBySourceChart(bySource = {}) {
  const map = bySource && typeof bySource === "object" ? bySource : {};
  return INCIDENT_SOURCE_ORDER.map((lane) => ({
    lane,
    label:
      lane === "threat_intel"
        ? "Threat Intel"
        : lane.charAt(0).toUpperCase() + lane.slice(1),
    count: map[lane] ?? 0,
  })).filter((row) => row.count > 0);
}

export function formatIncidentAge(isoString) {
  if (!isoString) return "—";
  const ms = Date.now() - new Date(isoString).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const mins = Math.floor(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

const ACTIVE_INCIDENT_STATUSES = new Set(["open", "investigating", "escalated"]);
const CRITICAL_HIGH_SEVERITIES = new Set(["critical", "high"]);

function decCount(value) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? Math.max(0, n - 1) : 0;
}

function incCount(value) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n + 1 : 1;
}

/** Optimistically adjust org-wide KPI summary after escalate/resolve. */
export function patchIncidentSummaryForMutation(summary, { action, previousStatus, severity }) {
  if (!summary || typeof summary !== "object") return summary;
  const prev = previousStatus || "";
  const next = { ...summary, by_source: { ...(summary.by_source || {}) } };
  const wasActive = ACTIVE_INCIDENT_STATUSES.has(prev);
  const wasCriticalHigh = wasActive && CRITICAL_HIGH_SEVERITIES.has(severity || "");

  if (action === "resolve") {
    if (prev) next[prev] = decCount(next[prev]);
    next.resolved = incCount(next.resolved);
    if (wasActive) next.active = decCount(next.active);
    if (wasCriticalHigh) next.critical_high = decCount(next.critical_high);
    return next;
  }

  if (action === "escalate" && prev !== "escalated") {
    if (prev) next[prev] = decCount(next[prev]);
    next.escalated = incCount(next.escalated);
  }
  return next;
}

function incidentRemovesFromFilteredView(previousStatus, filters = {}) {
  const { status: statusFilter = "", queue: queueFilter = "" } = filters;
  if (statusFilter && statusFilter === previousStatus) return true;
  return queueFilter === "active" && ACTIVE_INCIDENT_STATUSES.has(previousStatus);
}

export function applyIncidentListMutation(data, { incidentId, action, previousStatus, severity }, filters = {}) {
  if (!data || !incidentId) return data;
  const rowSeverity =
    severity || data.results?.find((r) => r.id === incidentId)?.severity || "";
  const next = { ...data };
  if (next.summary) {
    next.summary = patchIncidentSummaryForMutation(next.summary, {
      action,
      previousStatus,
      severity: rowSeverity,
    });
  }

  const removesFromView = incidentRemovesFromFilteredView(previousStatus, filters);
  if (action === "resolve") {
    if (removesFromView) {
      next.results = (next.results || []).filter((r) => r.id !== incidentId);
      next.count = Math.max(0, (next.count ?? 0) - 1);
    } else {
      next.results = (next.results || []).map((r) =>
        r.id === incidentId ? { ...r, status: "resolved" } : r,
      );
    }
  } else if (action === "escalate") {
    if (removesFromView && previousStatus !== "escalated") {
      next.results = (next.results || []).filter((r) => r.id !== incidentId);
      next.count = Math.max(0, (next.count ?? 0) - 1);
    } else {
      next.results = (next.results || []).map((r) =>
        r.id === incidentId ? { ...r, status: "escalated" } : r,
      );
    }
  }
  return next;
}

export function buildIncidentKpiItems(summary = {}, handlers = {}) {
  const {
    statusFilter = "",
    severityFilter = "",
    queueFilter = "",
    onStatusFilter,
    onSeverityFilter,
    onQueueFilter,
  } = handlers;
  const active = summary.active ?? 0;
  const criticalHigh = summary.critical_high ?? 0;

  return [
    {
      key: "active",
      label: "Active Queue",
      value: active,
      color: active > 0 ? "text-amber-600" : undefined,
      clickable: !!onQueueFilter,
      active: queueFilter === "active" && !statusFilter,
      onClick: () => onQueueFilter?.(queueFilter === "active" ? "" : "active"),
      helpText: "Cases still in progress (open, investigating, or escalated). Click to show only active work in the table.",
    },
    {
      key: "open",
      label: "Open",
      value: summary.open ?? 0,
      clickable: !!onStatusFilter,
      active: statusFilter === "open",
      onClick: () => onStatusFilter?.(statusFilter === "open" ? "" : "open"),
      helpText: "New cases awaiting first review. Click to filter the table to open incidents only.",
    },
    {
      key: "escalated",
      label: "Escalated",
      value: summary.escalated ?? 0,
      color: (summary.escalated ?? 0) > 0 ? "text-red-600" : undefined,
      clickable: !!onStatusFilter,
      active: statusFilter === "escalated",
      onClick: () => onStatusFilter?.(statusFilter === "escalated" ? "" : "escalated"),
      helpText: "Cases promoted for senior review or IR handoff.",
    },
    {
      key: "critical-high",
      label: "Critical / High",
      value: criticalHigh,
      color: criticalHigh > 0 ? "text-red-600" : undefined,
      clickable: !!onSeverityFilter,
      active: severityFilter === "high" || severityFilter === "critical",
      onClick: () => {
        if (severityFilter === "high" || severityFilter === "critical") {
          onSeverityFilter?.("");
        } else {
          onSeverityFilter?.("high");
        }
      },
      helpText: "Active incidents at high or critical severity among open work.",
    },
    {
      key: "resolved",
      label: "Resolved",
      value: summary.resolved ?? 0,
      color: "text-emerald-600",
      clickable: !!onStatusFilter,
      active: statusFilter === "resolved",
      onClick: () => onStatusFilter?.(statusFilter === "resolved" ? "" : "resolved"),
      helpText: "Closed incidents in the org queue (all time).",
    },
  ];
}

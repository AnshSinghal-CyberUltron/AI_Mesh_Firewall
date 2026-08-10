/** Pure data formatters for Module 2 page charts and tables. */

import { formatRiskBandLabel } from "../../utils/riskLabels.js";
import {
  EXPOSURE_KPI_SOURCE,
  INCIDENT_KPI_SOURCE,
  RAG_KPI_SOURCE,
  TELEMETRY_KPI_SOURCE,
  UEBA_KPI_SOURCE,
} from "../../utils/kpiDataSourceCopy.js";
import { stripModuleNumberPrefix } from "../../utils/module2DisplayNames.js";

/**
 * Prefer a customer-facing provenance `label` from the API when it reads as plain language.
 * Never surface internal identifiers such as `policy.SecurityIncident`.
 */
export function formatKpiDataSource(provenance, fallback = INCIDENT_KPI_SOURCE) {
  const label = typeof provenance?.label === "string" ? provenance.label.trim() : "";
  if (label && /\s/.test(label)) return label;
  return fallback;
}

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

export function buildRagKpis(ragPipelineKpis = {}, vectorExposure = {}, prePipelineDenials = {}) {
  // Prefer Module 1–aligned stages when present; fall back to stages on the payload root.
  const stages =
    ragPipelineKpis?.module1_aligned?.stages || ragPipelineKpis?.stages || {};
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

  const ingestEvents = ragPipelineKpis?.ingest_events || 0;
  // Primary KPI bar uses pipeline-stage rows only (event_type=rag_pipeline).
  void prePipelineDenials;
  const cards = [
    {
      key: "pipeline-events",
      label: "Pipeline Stage Events",
      value: pipelineIngress || totalStageChecks,
      dataSource: RAG_KPI_SOURCE,
      helpText:
        "How many times a RAG search moved through the safety checks (ask → find docs → rank → answer). Early access denials before those steps are not counted here.",
    },
    {
      key: "blocked-at-gate",
      label: "Blocked at Pipeline Gate",
      value: totalBlocked,
      color: totalBlocked > 0 ? "text-red-600" : undefined,
      dataSource: RAG_KPI_SOURCE,
      helpText:
        "How many of those RAG checks were stopped by the firewall during the search steps. This does not include requests blocked before search even started.",
    },
    {
      key: "collections",
      label: "Collections",
      value: collections.length,
      dataSource: RAG_KPI_SOURCE,
      helpText: "How many different document libraries were searched in this time window.",
    },
    {
      key: "hot-collections",
      label: "High-Risk Collections",
      value: hotCollections,
      color: hotCollections > 0 ? "text-amber-600" : undefined,
      dataSource: RAG_KPI_SOURCE,
      helpText:
        "Document libraries where half or more of searches were blocked. Worth a closer look for bad or overshared content.",
    },
    {
      key: "pass-rate",
      label: "Retriever Pass Rate",
      value: `${passRate}%`,
      color: passRate >= 80 ? "text-emerald-600" : passRate >= 50 ? "text-amber-600" : "text-red-600",
      dataSource: RAG_KPI_SOURCE,
      helpText: "Of searches that reached document lookup, how many were allowed to continue (not blocked).",
    },
  ];
  if (ingestEvents > 0) {
    cards.push({
      key: "ingest-events",
      label: "Ingest Events",
      value: ingestEvents,
      dataSource: RAG_KPI_SOURCE,
      helpText: "Times documents were added or updated in the knowledge base during this window.",
    });
  }
  return cards;
}

/** Module 2–only RAG extras (never mixed into primary Module 1–aligned KPIs). */
export function buildRagModule2Extras(ragData = {}) {
  const extra = ragData.module2_extra || ragData.rag_pipeline_kpis?.module2_extra || {};
  const denials =
    extra.pre_pipeline_denials || ragData.rag_pre_pipeline_denials || {};
  const ragQuery = extra.rag_query_in_query_stage || {};
  const denialTotal = Number(denials.total) || 0;
  const ragQueryTotal = Number(ragQuery.total) || 0;
  const hasExtras = denialTotal > 0 || ragQueryTotal > 0;
  return {
    label: extra.label || "Module 2 also includes (not in Module 1 stage KPIs)",
    hasExtras,
    matchMessage: "0 extras — totals match Module 1 for this window.",
    ragQuery: {
      total: ragQueryTotal,
      blocked: Number(ragQuery.blocked) || 0,
      allowed: Number(ragQuery.allowed) || 0,
      byAttributedStage: ragQuery.by_attributed_stage || {},
      reason:
        ragQuery.reason ||
        "Legacy rag_query events without rag_pipeline stage telemetry. Module 1 stage KPIs ignore these.",
    },
    denials: {
      total: denialTotal,
      label:
        denials.label ||
        "Pre-pipeline policy / access denials (extra vs Module 1 stage KPIs; aligns with Incidents Rag lane)",
      byEventType: denials.by_event_type || {},
      byStage: denials.by_stage || {},
    },
  };
}

/** Module 2–only MCP extras (MCPEvent not yet mirrored to EnforcementEvent). */
export function buildMcpModule2Extras(data = {}) {
  const extra = data.module2_extra || {};
  const summary = extra.summary || {};
  const total = Number(summary.total_events) || 0;
  const blocked = Number(summary.blocked) || 0;
  const redacted = Number(summary.redacted) || 0;
  return {
    label: extra.label || "Module 2 also includes (MCPEvent-only / pending mirror)",
    hasExtras: total > 0,
    matchMessage: "0 extras — MCP Risk matches Module 1 for this window.",
    summary: {
      total_events: total,
      blocked,
      redacted,
      reason:
        summary.reason ||
        "These tool calls are in the MCP audit log but not yet in the Module 1 EnforcementEvent feed.",
    },
  };
}

/** Top event types for the Health pre-pipeline denial card. */
export function formatRagDenialTypeRows(prePipelineDenials = {}) {
  const map = prePipelineDenials?.by_event_type || {};
  return Object.entries(map)
    .map(([eventType, count]) => ({
      eventType,
      count: Number(count) || 0,
    }))
    .filter((row) => row.count > 0)
    .sort((a, b) => b.count - a.count);
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
      sub: "All registered (full fleet)",
      dataSource: UEBA_KPI_SOURCE,
      helpText:
        "Every API key provisioned for this organization (not filtered by time). The fleet table below only lists keys with traffic in the selected window, or disabled / kill-switched keys — idle keys are omitted on purpose.",
    },
    {
      key: "active-keys",
      label: "Active Keys",
      value: s.active_keys ?? 0,
      color: "text-teal-600",
      sub: "Currently enabled",
      dataSource: UEBA_KPI_SOURCE,
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
      dataSource: UEBA_KPI_SOURCE,
      helpText: "Enforcement events attributed to API keys in the selected time window.",
    },
    {
      key: "blocked-events",
      label: "Blocked",
      value: s.blocked_events ?? 0,
      color: "text-red-600",
      sub: `Last ${periodLabel}`,
      dataSource: UEBA_KPI_SOURCE,
      helpText: "Hard-blocked requests from API keys in the selected time window.",
    },
    {
      key: "keys-with-activity",
      label: "Keys With Activity",
      value: s.keys_with_activity ?? 0,
      sub: `Last ${periodLabel}`,
      dataSource: UEBA_KPI_SOURCE,
      helpText: "Distinct API keys with at least one enforcement event in the selected window.",
    },
    {
      key: "high-risk-keys",
      label: "High behavioral risk keys",
      value: s.high_risk_keys ?? 0,
      color: "text-red-600",
      sub: `Active in ${periodLabel}`,
      dataSource: UEBA_KPI_SOURCE,
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
      dataSource: UEBA_KPI_SOURCE,
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
      dataSource: UEBA_KPI_SOURCE,
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
    { key: "active-models", label: "Active Models", value: summary.active_models ?? 0, dataSource: EXPOSURE_KPI_SOURCE, helpText: "Currently enabled Model Connection entries for this organization." },
    { key: "high-exposure", label: "High Exposure", value: summary.high_exposure_models ?? 0, color: "text-red-600", dataSource: EXPOSURE_KPI_SOURCE, helpText: "Models in the high exposure band—prioritize for policy review or routing changes." },
    { key: "total-requests", label: "Total Requests", value: summary.total_requests ?? 0, dataSource: EXPOSURE_KPI_SOURCE, helpText: "Aggregate request volume across all monitored models." },
    { key: "avg-block-rate", label: "Avg Block Rate", value: `${summary.avg_block_rate_pct ?? 0}%`, dataSource: EXPOSURE_KPI_SOURCE, helpText: "Fleet-wide mean block rate; sudden lifts may signal active attack campaigns." },
    { key: "avg-exposure", label: "Avg Exposure", value: summary.avg_exposure_score ?? 0, dataSource: EXPOSURE_KPI_SOURCE, helpText: "Mean composite exposure score (0–1). Higher values indicate elevated enforcement pressure." },
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

/** Fleet stats for the IOC library table — used on Threat Intel SOC panels. */
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

export function buildTelemetryKpis(summary = {}, iocLibrary = {}) {
  const libTotal = iocLibrary?.total;
  const libArmed = iocLibrary?.auto_block_enabled;
  const libSub =
    libTotal != null
      ? `${libTotal} IOCs in library${libArmed != null ? ` · ${libArmed} armed` : ""}`
      : undefined;

  return [
    {
      key: "total-events",
      label: "Gateway Requests",
      value: summary.requests_inspected ?? summary.total_events ?? 0,
      dataSource: TELEMETRY_KPI_SOURCE,
      helpText: "All gateway requests in this window (live enforcement telemetry — one count per request, aligned with gateway request totals).",
    },
    {
      key: "injection-attempts",
      label: "Injection & Jailbreak",
      value: summary.injection_attempts ?? 0,
      color: "text-red-600",
      dataSource: TELEMETRY_KPI_SOURCE,
      helpText: "All prompt-injection / jailbreak detections in this period (global gateway telemetry, not IOC-only).",
    },
    {
      key: "pii-leaks",
      label: "PII Detected",
      value: summary.pii_leaks ?? 0,
      color: "text-amber-600",
      dataSource: TELEMETRY_KPI_SOURCE,
      helpText: "All policy redaction detections in this period (global gateway telemetry, not IOC-only).",
    },
    {
      key: "behavior-scoring",
      label: "API Key Activity",
      value: summary.behavior_scoring_events ?? 0,
      color: "text-sky-600",
      dataSource: TELEMETRY_KPI_SOURCE,
      helpText: "All key-attributed gateway events in this period (feeds API Key & Identity Risk; not limited to Threat Intel sync).",
    },
    {
      key: "threat-intel-hits",
      label: "IOC Matches",
      value: summary.threat_intel_matches ?? 0,
      color: "text-violet-600",
      sub: libSub,
      dataSource: TELEMETRY_KPI_SOURCE,
      helpText: "Traffic that matched an indicator from your Threat Intel library (synced keyword or threat-intel policy path). Library size is shown below — matches rise only after live gateway enforcement.",
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

/** True when enforcement metadata reflects an IOC / threat-intel policy block (not generic scanner policy). */
export function isThreatIntelEnforcementMeta(meta = {}) {
  if (!meta || typeof meta !== "object") return false;
  const source = String(meta.source || "").toLowerCase();
  const extraDetail =
    meta.extra && typeof meta.extra === "object" ? String(meta.extra.detail || "") : "";
  const detail = String(meta.detail || extraDetail || "").toLowerCase();
  const tier = String(meta.detection_tier || "").toLowerCase();
  const code = String(meta.code || meta.blocked_by || meta.error_code || "").toLowerCase();
  const threatType = String(meta.threat_type || "").toLowerCase();
  if (
    source.includes("threat_intel")
    || tier === "threat_intel"
    || code === "threat_intel_blocked"
    || threatType.startsWith("threat_intel")
  ) {
    return true;
  }
  return (
    detail.includes("ioc match")
    || detail.includes("threat intelligence")
    || detail.includes("threat intel")
  );
}

/** Derive enforcement lane (chat/rag/vector/mcp/threat_intel) from WS or incident payloads. */
export function resolveEventLane(item = {}) {
  const topSource = String(item.source || "").toLowerCase();
  if (["chat", "rag", "vector", "mcp", "threat_intel"].includes(topSource)) {
    return topSource;
  }
  const meta = item.metadata || {};
  if (isThreatIntelEnforcementMeta(meta) || isThreatIntelEnforcementMeta(item)) {
    return "threat_intel";
  }
  const eventType = String(meta.event_type || "").toLowerCase();
  if (eventType === "mcp_tool_call" || eventType.startsWith("mcp_") || meta.tools_invoked || meta.mcp_server || meta.server_slug) {
    return "mcp";
  }
  if (eventType === "rag_pipeline" || eventType.startsWith("rag_")) return "rag";
  if (meta.collection || meta.vector_collection || meta.vector_namespace) return "vector";
  return "chat";
}

export function formatTickerHeadline(item = {}) {
  if (item.title) return stripModuleNumberPrefix(item.title) || item.title;
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
  if (item.message) return stripModuleNumberPrefix(item.message);
  const meta = item.metadata || {};
  const detail = metadataDetail(meta);
  if (detail) return stripModuleNumberPrefix(detail);
  if (item.timestamp) return item.timestamp.replace("T", " ").slice(0, 19);
  return "";
}

const TICKER_ACTION_PHRASES = {
  block: "was blocked by policy",
  redact: "was allowed after sensitive data was redacted",
  monitor: "was allowed but flagged for analyst review",
  flag: "was flagged for review",
  allow: "was allowed",
};

/** Analyst-facing block label distinguishing IOC policy blocks from generic policy/scanner blocks. */
export function formatEnforcementBlockLabel(meta = {}, action = "block") {
  if (String(action).toLowerCase() !== "block") return String(action || "processed");
  if (isThreatIntelEnforcementMeta(meta)) return "Threat Intel policy block";
  return "Policy block";
}

/** Plain-language one-liner for ticker hover / quick scan. */
export function formatTickerAnalystSummary(item = {}) {
  if (item.title) {
    const sev = item.severity ? `${item.severity} severity` : "unknown severity";
    const status = item.status || "open";
    return `Incident case (${sev}, ${status}): ${stripModuleNumberPrefix(item.title)}`;
  }

  const meta = item.metadata || {};
  const lane = resolveEventLane(item).replace(/_/g, " ");
  const action = String(item.action || meta.action || "").toLowerCase();
  let phrase = TICKER_ACTION_PHRASES[action] || (action ? `had outcome ${action}` : "was processed");
  if (action === "block" && isThreatIntelEnforcementMeta(meta)) {
    phrase = "was blocked by Threat Intelligence policy (IOC match)";
  }
  const parts = [`A ${lane} request ${phrase}`];

  const threat = meta.threat_type ? String(meta.threat_type).replace(/_/g, " ") : "";
  if (threat) parts.push(`threat: ${threat}`);

  const model = meta.model || meta.routed_model || meta.selected_model;
  if (model) parts.push(`model ${model}`);

  const key = meta.key_prefix || meta.api_key_prefix;
  if (key) parts.push(`key ${stripModuleNumberPrefix(key)}`);

  const detail = metadataDetail(meta);
  if (detail) parts.push(stripModuleNumberPrefix(detail));

  return parts.join(" · ");
}

/** Structured fields for expanded ticker rows (analyst forensics). */
export function buildTickerAnalystFields(item = {}) {
  if (item.title) {
    const fields = [
      { label: "Record type", value: "Security incident" },
      { label: "Title", value: stripModuleNumberPrefix(item.title) },
    ];
    if (item.severity) fields.push({ label: "Severity", value: item.severity });
    if (item.status) fields.push({ label: "Status", value: item.status });
    if (item.source) fields.push({ label: "Enforcement lane", value: String(item.source).replace(/_/g, " ") });
    if (item.created_at) {
      fields.push({ label: "Opened (UTC)", value: String(item.created_at).replace("T", " ").slice(0, 19) });
    }
    return fields.filter((f) => f.value);
  }

  const meta = item.metadata || {};
  const extra = meta.extra && typeof meta.extra === "object" ? meta.extra : {};
  const fields = [
    { label: "Enforcement lane", value: resolveEventLane(item).replace(/_/g, " ") },
  ];

  if (item.action) fields.push({ label: "Outcome", value: String(item.action).toUpperCase() });
  if (meta.threat_type) fields.push({ label: "Threat type", value: String(meta.threat_type).replace(/_/g, " ") });
  if (meta.owasp_code || meta.subcategory || item.subcategory) {
    fields.push({ label: "OWASP / category", value: meta.owasp_code || meta.subcategory || item.subcategory });
  }
  if (meta.model) fields.push({ label: "Model", value: meta.model });

  const original = meta.original_model || extra.original_model || extra.requested_model;
  const routed = meta.routed_model || meta.selected_model || extra.routed_model || extra.selected_model;
  if (original && routed) fields.push({ label: "Model routing", value: `${original} → ${routed}` });
  else if (meta.rerouted === true || extra.rerouted === true) {
    fields.push({ label: "Model routing", value: "Rerouted to alternate model" });
  }

  const key = meta.key_prefix || meta.api_key_prefix;
  if (key) fields.push({ label: "API key prefix", value: stripModuleNumberPrefix(key) });
  if (meta.pipeline_stage) fields.push({ label: "Pipeline stage", value: meta.pipeline_stage });
  if (meta.collection || meta.vector_collection) {
    fields.push({ label: "Vector collection", value: meta.collection || meta.vector_collection });
  }
  if (meta.mcp_server || meta.server_slug) {
    fields.push({ label: "MCP server", value: meta.mcp_server || meta.server_slug });
  }
  const tools = meta.tools_invoked;
  if (tools) {
    fields.push({
      label: "MCP tool",
      value: Array.isArray(tools) ? tools.join(", ") : String(tools),
    });
  }

  const detail = metadataDetail(meta);
  if (detail) fields.push({ label: "Reason", value: stripModuleNumberPrefix(detail) });

  const snippet = meta.prompt_snippet;
  if (snippet) fields.push({ label: "Prompt snippet", value: String(snippet).slice(0, 160) });

  if (meta.request_id) fields.push({ label: "Request ID", value: meta.request_id });
  if (item.id) fields.push({ label: "Event ID", value: String(item.id) });
  if (item.timestamp) {
    fields.push({ label: "Time (UTC)", value: String(item.timestamp).replace("T", " ").slice(0, 19) });
  }

  return fields.filter((f) => f.value);
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
  // Vector aggregates under RAG & retrieval — same badge family.
  if (source === "rag" || source === "vector") return "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300";
  if (source === "mcp") return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
  if (source === "chat") return "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300";
  return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
}

/** Read human-readable detail from enforcement metadata (top-level or nested extra). */
export function metadataDetail(meta = {}) {
  if (!meta || typeof meta !== "object") return "";
  for (const field of ["detail", "reason", "intent"]) {
    const value = String(meta[field] || "").trim();
    if (value) return value;
  }
  const extra = meta.extra;
  if (extra && typeof extra === "object") {
    for (const field of ["detail", "reason", "intent"]) {
      const value = String(extra[field] || "").trim();
      if (value) return value;
    }
  }
  return "";
}

const INCIDENT_LANE_DRILL_DOWN = {
  chat: { to: "/models/exposure", label: "Model exposure" },
  rag: { to: "/models/exposure?tab=rag", label: "RAG health" },
  vector: { to: "/models/exposure?tab=rag", label: "RAG health" },
  mcp: { to: "/mcp/risk", label: "MCP risk" },
  threat_intel: { to: "/threat-intel", label: "Threat intel" },
};

export function incidentLaneDrillDown(source) {
  return INCIDENT_LANE_DRILL_DOWN[source] || null;
}

/** Hub display order — Vector is folded into RAG (not a sibling card). */
const INCIDENT_SOURCE_ORDER = ["chat", "rag", "mcp", "threat_intel", "generic"];

/** Chart / chip labels — RAG aggregates pipeline + standalone collection lookups. */
export const INCIDENT_SOURCE_LABELS = {
  chat: "Chat",
  rag: "RAG & retrieval",
  vector: "RAG & retrieval",
  mcp: "MCP",
  threat_intel: "Threat Intel",
  generic: "Generic",
};

/** User-facing lane label — Vector aggregates under RAG & retrieval. */
export function formatLaneDisplayLabel(source) {
  const key = String(source || "generic").toLowerCase();
  return INCIDENT_SOURCE_LABELS[key] || key.replace(/_/g, " ");
}

export const INCIDENT_SOURCE_CHART_HELP =
  "Cases in the current filters and selected time window, grouped by enforcement lane. RAG & retrieval includes pipeline searches and document-library lookups.";

/**
 * Combine Hub lane_summary.rag + lane_summary.vector for one retrieval card.
 * Backend keeps both keys; UI aggregates so users see one retrieval total.
 */
export function mergeRagVectorLaneStats(laneSummary = {}) {
  const rag = laneSummary?.rag || {};
  const vector = laneSummary?.vector || {};
  const total = (Number(rag.total) || 0) + (Number(vector.total) || 0);
  const blocked = (Number(rag.blocked) || 0) + (Number(vector.blocked) || 0);
  const block_rate_pct = total > 0 ? Math.round((blocked / total) * 1000) / 10 : 0;
  return { total, blocked, block_rate_pct };
}

export function formatIncidentsBySourceChart(bySource = {}) {
  const map = bySource && typeof bySource === "object" ? { ...bySource } : {};
  const vectorCount = Number(map.vector) || 0;
  if (vectorCount > 0) {
    map.rag = (Number(map.rag) || 0) + vectorCount;
  }
  delete map.vector;
  return INCIDENT_SOURCE_ORDER.map((lane) => ({
    lane,
    label: INCIDENT_SOURCE_LABELS[lane] || lane.charAt(0).toUpperCase() + lane.slice(1),
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

/** Humanize threat_type / snake_case labels for analyst UI (kill_switch → Kill switch). */
export function humanizeThreatType(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const spaced = raw.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (!spaced) return "";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

/**
 * Extract display prompt for an incident timeline event.
 * Prefers top-level API prompt_snippet, then metadata / extra allowlisted fields.
 */
export function extractIncidentPrompt(event = {}) {
  const top = String(event.prompt_snippet || "").trim();
  if (top) return top;

  const meta = event.metadata && typeof event.metadata === "object" ? event.metadata : {};
  const extra = meta.extra && typeof meta.extra === "object" ? meta.extra : {};

  for (const field of [
    "prompt_snippet",
    "prompt_submitted",
    "input_text",
    "user_prompt",
    "original_prompt",
    "input_preview",
  ]) {
    const direct = String(meta[field] || "").trim();
    if (direct) return direct;
  }

  for (const field of [
    "prompt_snippet",
    "prompt",
    "user_message",
    "query",
    "prompt_submitted",
    "input_text",
  ]) {
    const nested = String(extra[field] || "").trim();
    if (nested) return nested;
  }

  const lineage = Array.isArray(meta.prompt_lineage) ? meta.prompt_lineage : [];
  for (const entry of lineage) {
    if (!entry || typeof entry !== "object") continue;
    const prompt = String(entry.prompt || entry.text || "").trim();
    if (prompt) return prompt;
  }

  return "";
}

function _formatBriefInstant(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return String(iso);
  return d.toLocaleString();
}

/**
 * Case time span for the Case Brief: opened → resolved/still open,
 * optional event window when the timeline has multiple timestamps.
 */
export function formatIncidentTimeSpan(incident = {}, timeline = []) {
  const opened = incident.created_at || null;
  const resolved = incident.resolved_at || null;
  const status = String(incident.status || "").toLowerCase();
  const openedLabel = _formatBriefInstant(opened) || "—";
  const closedLabel =
    resolved
      ? _formatBriefInstant(resolved)
      : status === "resolved"
        ? "Resolved"
        : "Still open";

  const times = (Array.isArray(timeline) ? timeline : [])
    .map((ev) => ev?.created_at)
    .filter(Boolean)
    .map((iso) => new Date(iso).getTime())
    .filter((t) => Number.isFinite(t))
    .sort((a, b) => a - b);

  let eventWindow = null;
  if (times.length >= 2) {
    eventWindow = `${_formatBriefInstant(new Date(times[0]).toISOString())} → ${_formatBriefInstant(new Date(times[times.length - 1]).toISOString())}`;
  } else if (times.length === 1) {
    eventWindow = _formatBriefInstant(new Date(times[0]).toISOString());
  }

  return {
    opened: openedLabel,
    closed: closedLabel,
    caseSpan: `${openedLabel} → ${closedLabel}`,
    eventWindow,
    isOpen: !resolved && status !== "resolved",
  };
}

const ACTION_BADGE_CLASS = {
  block: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  redact: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  monitor: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
  flag: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
  allow: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200",
};

/** Colored badge class for enforcement action. */
export function incidentActionBadgeClass(action) {
  const key = String(action || "").toLowerCase();
  return ACTION_BADGE_CLASS[key] || ACTION_BADGE_CLASS.monitor;
}

/** Short human phrase for the Case Brief action chip. */
export function formatIncidentActionPhrase(action, meta = {}) {
  const key = String(action || "").toLowerCase();
  if (key === "block" && isThreatIntelEnforcementMeta(meta)) {
    return "Blocked by Threat Intelligence";
  }
  if (TICKER_ACTION_PHRASES[key]) {
    // "was blocked by policy" → "Blocked by policy"
    const phrase = TICKER_ACTION_PHRASES[key];
    return phrase.replace(/^was\s+/i, "").replace(/^\w/, (c) => c.toUpperCase());
  }
  return key ? key.charAt(0).toUpperCase() + key.slice(1) : "Processed";
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
  if (action === "investigate" && prev === "open") {
    next.open = decCount(next.open);
    next.investigating = incCount(next.investigating);
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
  } else if (action === "investigate") {
    const statusFilter = filters.status || "";
    if (statusFilter === "open") {
      next.results = (next.results || []).filter((r) => r.id !== incidentId);
      next.count = Math.max(0, (next.count ?? 0) - 1);
    } else {
      next.results = (next.results || []).map((r) =>
        r.id === incidentId ? { ...r, status: "investigating" } : r,
      );
    }
  }
  return next;
}

export function buildIncidentKpiItems(summary = {}, handlers = {}, dataProvenance = null) {
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
  const dataSource = formatKpiDataSource(dataProvenance, INCIDENT_KPI_SOURCE);

  return [
    {
      key: "active",
      label: "Active Queue",
      value: active,
      color: active > 0 ? "text-amber-600" : undefined,
      dataSource,
      clickable: !!onQueueFilter,
      active: queueFilter === "active" && !statusFilter,
      onClick: () => onQueueFilter?.(queueFilter === "active" ? "" : "active"),
      helpText: "Cases still in progress (open, investigating, or escalated). Click to show only active work in the table.",
    },
    {
      key: "open",
      label: "Open",
      value: summary.open ?? 0,
      dataSource,
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
      dataSource,
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
      dataSource,
      clickable: !!onSeverityFilter,
      active: severityFilter === "critical_high",
      onClick: () => {
        if (severityFilter === "critical_high") {
          onSeverityFilter?.("");
        } else {
          onSeverityFilter?.("critical_high");
        }
      },
      helpText: "Active incidents at high or critical severity (open work only). Click to show the active queue filtered to those severities.",
    },
    {
      key: "resolved",
      label: "Resolved",
      value: summary.resolved ?? 0,
      color: "text-emerald-600",
      dataSource,
      clickable: !!onStatusFilter,
      active: statusFilter === "resolved",
      onClick: () => onStatusFilter?.(statusFilter === "resolved" ? "" : "resolved"),
      helpText: "Closed incidents in the org queue for the selected time window. Stays accurate when you filter the table.",
    },
  ];
}

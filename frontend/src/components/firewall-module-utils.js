import {
  getRequestedModel as routingRequestedModel,
  getRoutedModel as routingRoutedModel,
  getRoutingContext,
} from "../utils/routingEventFields.js";

const MODULE_FILTERS = {
  "1.1": {},
  "1.2": {
    eventTypes: ["rag_pipeline"],
    threatTypes: ["data_leakage", "pii", "rag_poisoning"],
    owaspPrefixes: ["LLM06", "LLM08"],
  },
  "1.3": {
    // RAG/vector events are stamped event_type=rag_pipeline (+ embedding/vector
    // queries). The old filter only matched rag_poisoning/LLM08, which no live
    // event carries, so the whole 1.3 page rendered empty. Match the real RAG
    // event types so vector-firewall evidence actually surfaces.
    eventTypes: ["rag_pipeline", "embedding_request", "vector_query"],
    threatTypes: ["rag_poisoning"],
    owaspPrefixes: ["LLM08"],
  },
  "1.4": {
    sources: ["mcp_scan"],
    owaspPrefixes: ["MCP"],
  },
  "1.5": {
    sources: ["routing", "agentic_scan"],
    eventTypes: ["model_routed"],
    owaspPrefixes: ["AGENTIC"],
  },
  "1.6": {
    moduleId: "1.6",
    eventTypes: ["kill_switch", "model_isolation", "circuit_breaker"],
    threatTypes: ["kill_switch", "model_isolation", "model_state_unavailable", "model_isolated"],
  },
  "1.7": {
    eventTypes: ["output_guard", "output_scan"],
  },
};

const MODULE_PAGE_CONFIG = {
  "1.1": {
    badge: "Unified gateway lane",
    workspaceTitle: "Gateway controls and full mesh intake testing",
    workspaceDescription: "This is the broadest Module 1 surface: it represents the unified gateway sitting in front of all model, RAG, MCP, and output traffic.",
    analyticsTitle: "Gateway-wide pressure and enforcement telemetry",
    analyticsDescription: "Gateway analytics intentionally cover the full Module 1 intake surface so operators can see pressure across the whole mesh entry point.",
    evidenceTitle: "Recent gateway evidence",
    evidenceDescription: "Identity, source, and model context for the most recent events seen by the unified gateway.",
    focusAreas: [
      "Track auth failures and rate-limit pressure before requests hit downstream models.",
      "Keep gateway key changes adjacent to live attack simulations.",
      "Use ingress evidence to validate that identity and quota policy are actually attached.",
    ],
    panelLabels: {
      control: "Ingress controls",
      simulator: "Traffic exercises",
      inspection: "Live ingress inspection",
      secondary: "Supporting surfaces",
    },
    columns: [
      { label: "Time", value: (event) => formatTimestamp(event.timestamp) },
      { label: "Project", value: (event) => getMetadata(event).project_id || getMetadata(event).organization_id || event.endpoint_name || "--" },
      { label: "Action", kind: "action", value: (event) => event.action || "allow" },
      { label: "Risk", kind: "severity", value: (event) => getRiskScore(event) || "--" },
      { label: "Source", value: (event) => getSource(event) || "gateway" },
      { label: "Model", value: (event) => getMetadata(event).model || getMetadata(event).routed_model || "--" },
    ],
  },
  "1.2": {
    badge: "Unified policy lane",
    workspaceTitle: "RAG and vector policy controls",
    workspaceDescription: "Keep content rules and vector isolation policy in one workspace so operators can tune enforcement without hopping between retrieval pages.",
    analyticsTitle: "Policy coverage and enforcement telemetry",
    analyticsDescription: "This page prioritizes rule posture, policy breadth, and enforcement outcomes instead of simulator-first workflow panels.",
    evidenceTitle: "Recent policy enforcement evidence",
    evidenceDescription: "Operator-facing evidence showing where configured RAG and vector policies blocked, redacted, or monitored traffic.",
    focusAreas: [
      "Lead with editable rules and vector access policies, not simulators.",
      "Keep content-policy and namespace-policy changes adjacent so operators can tune both layers together.",
      "Use the evidence table to confirm whether policy changes are reducing retrieval-stage risk.",
    ],
    panelLabels: {
      control: "Policy controls",
      simulator: "Policy exercises",
      inspection: "Policy inspection",
      secondary: "Supporting surfaces",
    },
    columns: [
      { label: "Time", value: (event) => formatTimestamp(event.timestamp) },
      { label: "Stage", value: (event) => titleCase(getStage(event) || getMetadata(event).event_type || "query") },
      { label: "Threat", value: (event) => formatThreat(event) },
      { label: "Action", kind: "action", value: (event) => event.action || "allow" },
      { label: "OWASP", value: (event) => getMetadata(event).owasp_code || "--" },
      { label: "Model", value: (event) => getMetadata(event).model || getMetadata(event).routed_model || "--" },
    ],
  },
  "1.3": {
    badge: "RAG + vector operations lane",
    workspaceTitle: "RAG pipeline and Vector DB workflow",
    workspaceDescription: "Keep database connectivity, stage controls, ingestion, and both simulators on one page so retrieval workflows can be tested end to end.",
    analyticsTitle: "RAG and vector retrieval telemetry",
    analyticsDescription: "The analytics lane emphasizes end-to-end retrieval security across query rewriting, vector access, and generator handoff.",
    evidenceTitle: "Recent RAG and vector evidence",
    evidenceDescription: "Collection, namespace, and retrieval-stage context for the most recent combined RAG and Vector DB firewall decisions.",
    focusAreas: [
      "Keep connectivity, stage controls, and simulators in one operating lane.",
      "Show retrieval-stage evidence before generic intervention charts.",
      "Let operators validate secure ingestion, pipeline checks, and vector query behavior without changing tabs.",
    ],
    panelLabels: {
      control: "RAG + Vector controls",
      simulator: "RAG + Vector exercises",
      inspection: "Retrieval inspection",
      secondary: "Supporting surfaces",
    },
    columns: [
      { label: "Time", value: (event) => formatTimestamp(event.timestamp) },
      { label: "Collection", value: (event) => getMetadata(event).collection || getMetadata(event).vector_collection || getExtra(event).collection || "--" },
      { label: "Namespace", value: (event) => getMetadata(event).namespace || getMetadata(event).vector_namespace || getExtra(event).namespace || "--" },
      { label: "Action", kind: "action", value: (event) => event.action || "allow" },
      { label: "Threat", value: (event) => formatThreat(event) },
      { label: "Risk", kind: "severity", value: (event) => getRiskScore(event) || "--" },
    ],
  },
  "1.4": {
    badge: "Context assembly lane",
    workspaceTitle: "Context minimization and MCP guardrails",
    workspaceDescription: "Operators can review tool scope, redaction pressure, and device context without leaving the MCP control surface.",
    analyticsTitle: "Context assembly and tool-governance telemetry",
    analyticsDescription: "This lane prioritizes context redaction, tool usage, and MCP evidence instead of repeating the generic firewall comparison charts.",
    evidenceTitle: "Recent MCP and context evidence",
    evidenceDescription: "Tool invocations, data-access hints, and redaction pressure from recent context assembly flows.",
    focusAreas: [
      "Lead with least-privilege context assembly and tool scope.",
      "Keep scanner output, device state, and MCP simulations visible together.",
      "Make redaction pressure legible by surfacing tools invoked and data scopes accessed in the evidence table.",
    ],
    panelLabels: {
      control: "Context controls",
      simulator: "Guardrail exercises",
      inspection: "Context inspection",
      secondary: "Supporting surfaces",
    },
    columns: [
      { label: "Time", value: (event) => formatTimestamp(event.timestamp) },
      { label: "Threat", value: (event) => formatThreat(event) },
      { label: "Tools", value: (event) => joinList(getToolsInvoked(event)) || "--" },
      { label: "Data Scope", value: (event) => joinList(getDataAccessed(event)) || "--" },
      { label: "Action", kind: "action", value: (event) => event.action || "allow" },
      { label: "Risk", kind: "severity", value: (event) => getRiskScore(event) || "--" },
    ],
  },
  "1.5": {
    badge: "Model routing lane",
    workspaceTitle: "Routing policy, provider state, and failover validation",
    workspaceDescription: "Model governance should read like a mesh-control page: routing decisions, provider health, and failover evidence first.",
    analyticsTitle: "Routing decisions and provider governance telemetry",
    analyticsDescription: "These visuals focus on requested-vs-routed model behavior and failover pressure rather than reusing the generic firewall event frame.",
    evidenceTitle: "Recent routing evidence",
    evidenceDescription: "Requested model, routed model, and enforcement context for the latest model-governance decisions.",
    focusAreas: [
      "Treat routing as a provider-governance surface, not a generic event feed.",
      "Make failover and model substitution visible from the first screen.",
      "Keep model connection controls adjacent to routing evidence so operators can correlate policy with provider state.",
    ],
    panelLabels: {
      control: "Routing controls",
      simulator: "Routing exercises",
      inspection: "Routing inspection",
      secondary: "Supporting surfaces",
    },
    columns: [
      { label: "Time", value: (event) => formatTimestamp(event.timestamp) },
      { label: "Requested", value: (event) => getRequestedModel(event) || "--" },
      { label: "Routed", value: (event) => getRoutedModel(event) || "--" },
      { label: "Context", value: (event) => getRoutingContext(event) || "--" },
      { label: "Action", kind: "action", value: (event) => event.action || "allow" },
      { label: "Risk", kind: "severity", value: (event) => getRiskScore(event) || "--" },
      { label: "Source", value: (event) => getSource(event) || "--" },
    ],
  },
  "1.6": {
    badge: "Isolation and kill-switch lane",
    workspaceTitle: "Containment controls and critical-response workflow",
    workspaceDescription: "This page should feel like an incident board: thresholds, kill-switch controls, model state, and critical evidence front and center.",
    analyticsTitle: "Isolation and kill-switch telemetry",
    analyticsDescription: "Analytics cover gateway detections and control-plane containment actions in the current time window.",
    evidenceTitle: "Recent critical evidence",
    evidenceDescription: "The highest-risk model events, containment actions, and kill-switch-adjacent evidence in the current time window.",
    focusAreas: [
      "Lead with containment readiness and critical-event load.",
      "Keep the circuit-breaker simulator close to the kill-switch controls.",
      "Use evidence rows to understand which models are repeatedly tripping isolation conditions.",
    ],
    panelLabels: {
      control: "Containment controls",
      simulator: "Containment exercises",
      inspection: "Critical inspection",
      secondary: "Supporting surfaces",
    },
    columns: [
      { label: "Time", value: (event) => formatTimestamp(event.timestamp) },
      { label: "Model", value: (event) => getMetadata(event).model || getRoutedModel(event) || "--" },
      { label: "Origin", value: (event) => getEventOrigin(event) },
      { label: "Action", kind: "action", value: (event) => event.action || "allow" },
      { label: "Risk", kind: "severity", value: (event) => getRiskScore(event) || "--" },
      { label: "Trigger", value: (event) => titleCase(getMetadata(event).event_type || formatThreat(event)) },
      { label: "Operator", value: (event) => getEventOperator(event) },
    ],
  },
  "1.7": {
    badge: "Output guardrail lane",
    workspaceTitle: "Output governance and response-path enforcement",
    workspaceDescription: "Real-time output scanning with full transparency: every model response is inspected for PII, credentials, hallucinations, and IP leakage. The engine card shows detection categories and action distribution. The governance log surfaces prompt, raw output, detected risks, and enforcement action for every event.",
    analyticsTitle: "Output filtering and enforcement telemetry",
    analyticsDescription: "Post-generation analytics prioritize block, redact, and flag decisions across the response path with detection confidence and compliance tagging.",
    evidenceTitle: "Recent output-governance evidence",
    evidenceDescription: "Latest response-path actions, detection categories, and output-level enforcement decisions.",
    focusAreas: [
      "Use the engine card to confirm which detection categories are actively firing.",
      "Use output-specific evidence fields so response-path decisions read differently from ingress events.",
      "Make blocked-vs-redacted-vs-flagged outcomes obvious before operators drill into raw JSON.",
    ],
    panelLabels: {
      control: "Output governance",
      simulator: "Output exercises",
      inspection: "Response inspection",
      secondary: "Supporting surfaces",
    },
    columns: [
      { label: "Time", value: (event) => formatTimestamp(event.timestamp) },
      { label: "Threat", value: (event) => formatThreat(event) },
      { label: "Action", kind: "action", value: (event) => event.action || "allow" },
      { label: "Risk", kind: "severity", value: (event) => getRiskScore(event) || "--" },
      { label: "OWASP", value: (event) => getMetadata(event).owasp_code || "--" },
      { label: "Model", value: (event) => getMetadata(event).model || getRoutedModel(event) || "--" },
    ],
  },
};

export function filterEventsForModule(moduleId, threatFeed = []) {
  const filters = MODULE_FILTERS[moduleId] || {};
  const events = Array.isArray(threatFeed) ? threatFeed : [];

  return events.filter((event) => matchesModuleFilters(event, filters, moduleId));
}

export function buildModulePageData(moduleId, threatFeed = [], extras = {}) {
  const config = MODULE_PAGE_CONFIG[moduleId] || MODULE_PAGE_CONFIG["1.1"];
  const events = filterEventsForModule(moduleId, threatFeed);
  const summary = summarizeEvents(events);
  const rows = buildRows(config.columns, events);

  return {
    config,
    events,
    summary,
    rows,
    summaryCards: buildSummaryCards(moduleId, summary, events, extras),
    spotlightCards: buildSpotlightCards(moduleId, summary, events, extras),
  };
}

function matchesModuleFilters(event, filters, moduleId) {
  if (!filters || Object.keys(filters).length === 0) {
    return true;
  }

  const meta = getMetadata(event);
  const source = getSource(event).toLowerCase();
  const owasp = String(meta.owasp_code || "").toUpperCase();
  const threatType = String(meta.threat_type || "").toLowerCase();
  const eventType = String(meta.event_type || "").toLowerCase();
  const riskScore = getRiskScore(event);

  if (filters.criticalOnly && riskScore >= 80) {
    return true;
  }

  if (filters.moduleId) {
    const mid = String(filters.moduleId);
    if (meta.module_id === mid || meta.module === mid) {
      return true;
    }
    if (meta.is_isolation_event || meta.is_audit_log) {
      return true;
    }
  }

  const matchedSource = filters.sources?.some((item) => source === String(item).toLowerCase()) || false;
  const matchedThreatType = filters.threatTypes?.some((item) => threatType === String(item).toLowerCase()) || false;
  const matchedEventType = filters.eventTypes?.some((item) => eventType === String(item).toLowerCase()) || false;
  const matchedOwasp = filters.owaspPrefixes?.some((prefix) => owasp.startsWith(String(prefix).toUpperCase())) || false;

  if (matchedSource || matchedThreatType || matchedEventType || matchedOwasp) {
    return true;
  }

  return false;
}

function buildRows(columns, events) {
  return events.slice(0, 8).map((event, index) => ({
    id: event.id || event.timestamp || `${index}`,
    raw: buildLogDetailPayload(event),
    cells: columns.map((column) => ({
      label: column.label,
      kind: column.kind,
      value: column.value(event),
    })),
  }));
}

function buildLogDetailPayload(event) {
  const meta = getMetadata(event);
  return {
    ...event,
    metadata: meta,
    raw: event,
    timestamp: event.timestamp,
    action: event.action,
    severity: event.severity ?? meta.security_risk_score ?? 0,
    source: event.source || meta.source || "",
  };
}

function deriveSocKpiSummary(socKpis) {
  if (!socKpis) return null;
  const total = Number(socKpis.total_threats) || 0;
  const blocked = Number(socKpis.blocked) || 0;
  const redacted = Number(socKpis.redacted) || 0;
  return {
    total,
    blocked,
    redacted,
    allowed: Math.max(0, total - blocked - redacted),
  };
}

function buildSummaryCards(moduleId, summary, events, extras) {
  const socDerived = moduleId === "1.1" ? deriveSocKpiSummary(extras.socKpis) : null;
  const numeric = socDerived || summary;
  const base = {
    total: fmtCount(numeric.total),
    blocked: fmtCount(numeric.blocked),
    redacted: fmtCount(numeric.redacted),
    flagged: fmtCount(summary.flagged),
    critical: fmtCount(summary.critical),
    allowed: fmtCount(numeric.allowed),
    monitor: fmtCount(summary.monitor),
  };

  switch (moduleId) {
    case "1.1": {
      const identities = uniqueCount(events.map((event) => getMetadata(event).project_id || getMetadata(event).organization_id || getMetadata(event).tenant_id));
      const periodLabel = extras.socKpis?.period ? ` (${extras.socKpis.period})` : "";
      return [
        {
          label: "Requests inspected",
          value: base.total,
          detail: `All ingress events in the selected lens${periodLabel} — same source as overview Total events`,
        },
        {
          label: "Allowed through gateway",
          value: base.allowed,
          detail: socDerived
            ? `Completed without block or redact (${base.redacted} redacted in period)`
            : "Events that completed without a hard intervention",
        },
        {
          label: "Rate-limited or blocked",
          value: base.blocked,
          detail: "Gateway events stopped before downstream completion",
        },
        {
          label: "Identities observed",
          value: fmtCount(identities),
          detail: "Distinct tenants in recent evidence sample (table may show up to 500 rows)",
        },
      ];
    }
    case "1.2": {
      const stages = uniqueCount(events.map((event) => getStage(event) || getMetadata(event).event_type));
      const ragDocs = extras.ragPipelineKpis?.documents_scanned || extras.ragPipelineKpis?.retrievals_scanned || 0;
      return [
        { label: "Pipeline events", value: base.total, detail: "Module-filtered RAG actions across query to generator" },
        { label: "Pipeline blocks", value: base.blocked, detail: "RAG stages prevented from reaching the next checkpoint" },
        { label: "Stages active", value: fmtCount(stages), detail: "Distinct pipeline stages visible in recent events" },
        { label: "Docs touched", value: fmtCount(ragDocs), detail: "Backend-reported retrieval or ingestion count in the current lens" },
      ];
    }
    case "1.3": {
      const collections = uniqueCount(events.map((event) => getMetadata(event).collection || getMetadata(event).vector_collection || getExtra(event).collection));
      const namespaces = uniqueCount(events.map((event) => getMetadata(event).namespace || getMetadata(event).vector_namespace || getExtra(event).namespace));
      return [
        { label: "Vector queries", value: base.total, detail: "Vector-specific events matched to this page" },
        { label: "Isolation blocks", value: base.blocked, detail: "Queries rejected before cross-tenant or risky retrieval completed" },
        { label: "Collections touched", value: fmtCount(collections), detail: "Distinct vector collections seen in recent evidence" },
        { label: "Namespaces involved", value: fmtCount(namespaces), detail: "Tenant or namespace boundaries referenced in recent events" },
      ];
    }
    case "1.4": {
      const tools = uniqueCount(events.flatMap((event) => getToolsInvoked(event)));
      const scopes = uniqueCount(events.flatMap((event) => getDataAccessed(event)));
      return [
        { label: "Context events", value: base.total, detail: "MCP and context assembly decisions in the current lens" },
        { label: "Redactions applied", value: base.redacted, detail: "Context fields or tool outputs sanitized before assembly" },
        { label: "Tools invoked", value: fmtCount(tools), detail: "Distinct MCP tools observed in recent activity" },
        { label: "Data scopes", value: fmtCount(scopes), detail: "Unique data scopes or assets referenced during context assembly" },
      ];
    }
    case "1.5": {
      const requestedModels = uniqueCount(events.map((event) => getRequestedModel(event)));
      const routedModels = uniqueCount(events.map((event) => getRoutedModel(event)));
      const failovers = events.filter((event) => {
        const requested = getRequestedModel(event);
        const routed = getRoutedModel(event);
        return requested && routed && requested !== routed;
      }).length;
      return [
        { label: "Routing decisions", value: base.total, detail: "Model-governance events in the current time lens" },
        { label: "Failovers or reroutes", value: fmtCount(failovers), detail: "Requests that landed on a different model than requested" },
        { label: "Requested models", value: fmtCount(requestedModels), detail: "Distinct requested models seen in recent routing evidence" },
        { label: "Routed models", value: fmtCount(routedModels), detail: "Distinct execution targets in the current routing stream" },
      ];
    }
    case "1.6": {
      const affectedModels = uniqueCount(events.map((event) => getMetadata(event).model || getRoutedModel(event)));
      const containmentEvents = events.filter((event) => {
        const eventType = String(getMetadata(event).event_type || "").toLowerCase();
        return eventType.includes("kill") || eventType.includes("circuit") || event.action === "block";
      }).length;
      return [
        { label: "Isolation events", value: base.total, detail: "Gateway detections and control-plane containment actions" },
        { label: "Critical (≥80)", value: base.critical, detail: "Highest-risk events within the isolation stream" },
        { label: "Containment actions", value: fmtCount(containmentEvents), detail: "Kill-switch, circuit-breaker, or hard-block events" },
        { label: "Affected models", value: fmtCount(affectedModels), detail: "Distinct models appearing in critical evidence" },
        { label: "Allowed despite risk", value: base.allowed, detail: "High-risk events that still resolved without a hard block" },
      ];
    }
    case "1.7": {
      const reviewCandidates = events.filter((event) => ["flag", "monitor"].includes(String(event.action || "").toLowerCase()) || getMetadata(event).review_required).length;
      return [
        { label: "Outputs scanned", value: base.total, detail: "Post-generation events matched to output guardrails" },
        { label: "Outputs blocked", value: base.blocked, detail: "Responses stopped before leaving the gateway" },
        { label: "Outputs redacted", value: base.redacted, detail: "Responses sanitized instead of blocked" },
        { label: "Outputs flagged", value: base.flagged, detail: "IP-leakage / hallucination flagged for review, not blocked" },
        { label: "Review triggers", value: fmtCount(reviewCandidates), detail: "Flagged or monitored outputs requiring human review" },
      ];
    }
    default:
      return [
        { label: "Events", value: base.total, detail: "Recent events" },
        { label: "Blocked", value: base.blocked, detail: "Hard interventions" },
        { label: "Redacted", value: base.redacted, detail: "Sanitized responses" },
        { label: "Critical", value: base.critical, detail: "High-risk events" },
      ];
  }
}

function buildSpotlightCards(moduleId, summary, events, extras) {
  // /api/gateways/stats/ returns a bare JSON array, so prefer .length; fall back
  // to {count}/{results} shapes for forward-compat. Without the Array check this
  // card was permanently 0 even when gateways exist.
  const gatewayHealth = Array.isArray(extras.gatewayStats)
    ? extras.gatewayStats.length
    : (extras.gatewayStats?.count || extras.gatewayStats?.results?.length || 0);
  const avgRisk = average(events.map((event) => getRiskScore(event)).filter(Boolean));
  const latestSource = events[0] ? getSource(events[0]) || "--" : "--";

  switch (moduleId) {
    case "1.1":
      return [
        { label: "Gateway assets", value: fmtCount(gatewayHealth), detail: "Gateway records available from the control plane" },
        { label: "Average risk", value: fmtPercent(avgRisk), detail: "Mean security risk score across recent ingress events" },
        { label: "Latest source", value: latestSource, detail: "Most recent ingress source observed in the current lens" },
      ];
    case "1.2": {
      const stageCounts = countBy(events.map((event) => getStage(event) || "query"));
      const hottestStage = topKey(stageCounts);
      return [
        { label: "Hottest stage", value: titleCase(hottestStage || "query"), detail: "Stage with the most recent RAG pressure" },
        { label: "Average risk", value: fmtPercent(avgRisk), detail: "Risk score across recent RAG evidence" },
        { label: "Pipeline source", value: latestSource, detail: "Latest source observed in the RAG activity stream" },
      ];
    }
    case "1.3": {
      const collections = countBy(events.map((event) => getMetadata(event).collection || getMetadata(event).vector_collection || getExtra(event).collection || "unknown"));
      return [
        { label: "Busiest collection", value: titleCase(topKey(collections) || "unknown"), detail: "Collection with the most recent vector traffic" },
        { label: "Average risk", value: fmtPercent(avgRisk), detail: "Risk score across vector-specific events" },
        { label: "Latest source", value: latestSource, detail: "Latest vector-related source observed in the evidence feed" },
      ];
    }
    case "1.4": {
      const tools = countBy(events.flatMap((event) => getToolsInvoked(event)));
      return [
        { label: "Most-invoked tool", value: titleCase(topKey(tools) || "none"), detail: "Tool creating the most recent MCP pressure" },
        { label: "Average risk", value: fmtPercent(avgRisk), detail: "Risk score across recent context assembly events" },
        { label: "Latest source", value: latestSource, detail: "Most recent MCP or context source observed in the lens" },
      ];
    }
    case "1.5": {
      const routed = countBy(events.map((event) => getRoutedModel(event) || "unknown"));
      return [
        { label: "Primary routed model", value: titleCase(topKey(routed) || "unknown"), detail: "Most common execution target in recent routing evidence" },
        { label: "Average risk", value: fmtPercent(avgRisk), detail: "Mean risk score across model-governance decisions" },
        { label: "Latest source", value: latestSource, detail: "Most recent routing source or governance emitter" },
      ];
    }
    case "1.6": {
      const models = countBy(events.map((event) => getMetadata(event).model || getRoutedModel(event) || "unknown"));
      return [
        { label: "Most affected model", value: titleCase(topKey(models) || "unknown"), detail: "Model appearing most often in critical evidence" },
        { label: "Average risk", value: fmtPercent(avgRisk), detail: "Mean risk score across isolation evidence" },
        { label: "Latest trigger", value: latestSource, detail: "Most recent critical source in the isolation stream" },
      ];
    }
    case "1.7": {
      const threatTypes = countBy(events.map((event) => getMetadata(event).threat_type || event.category || "unknown"));
      return [
        { label: "Top trigger", value: titleCase(topKey(threatTypes) || "unknown"), detail: "Most common recent output-guardrail trigger" },
        { label: "Average risk", value: fmtPercent(avgRisk), detail: "Risk score across recent response-path evidence" },
        { label: "Latest source", value: latestSource, detail: "Most recent output-stage source observed in the lens" },
      ];
    }
    default:
      return [];
  }
}

function summarizeEvents(events) {
  const actions = countBy(events.map((event) => String(event.action || "allow").toLowerCase()));
  const critical = events.filter((event) => getRiskScore(event) >= 80).length;
  const total = events.length;
  const blocked = actions.block || 0;
  const redacted = actions.redact || 0;
  const flagged = actions.flag || 0;
  const monitor = actions.monitor || 0;
  const allowed = Math.max(0, total - blocked - redacted - flagged - monitor);

  return {
    total,
    blocked,
    redacted,
    flagged,
    critical,
    monitor,
    allowed,
  };
}

export function getModulePageConfig(moduleId) {
  return MODULE_PAGE_CONFIG[moduleId] || MODULE_PAGE_CONFIG["1.1"];
}

export function getModuleEvidenceEmptyMessage(moduleId) {
  if (moduleId === "1.6") {
    return "No isolation or kill-switch evidence in this time range. Use the containment simulator to generate a live event, or widen the time lens.";
  }
  return "No recent module evidence is available for this time range.";
}

function getEventOrigin(event) {
  const meta = getMetadata(event);
  if (meta.is_audit_log || event.source_display === "control_plane" || event.record_type === "control_plane_audit") {
    return "Control plane";
  }
  return "Gateway";
}

function getEventOperator(event) {
  const meta = getMetadata(event);
  if (meta.is_audit_log) {
    return meta.triggered_by || event.user_display || "control_plane";
  }
  return event.user_display || meta.triggered_by || "--";
}

function getMetadata(event) {
  return event?.metadata || {};
}

// The control plane nests the gateway's raw telemetry metadata under
// `metadata.extra` (see control core/tasks.py). Vector collection/namespace
// and similar gateway-emitted fields live there, not at the top level.
function getExtra(event) {
  return getMetadata(event).extra || {};
}

function getSource(event) {
  return String(event?.source || getMetadata(event).source || "");
}

function getRiskScore(event) {
  const meta = getMetadata(event);
  const raw = meta.security_risk_score ?? event?.severity ?? 0;
  const number = Number(raw);
  return Number.isFinite(number) ? number : 0;
}

function getStage(event) {
  const meta = getMetadata(event);
  return String(meta.pipeline_stage || meta.stage || "").toLowerCase();
}

function getRequestedModel(event) {
  return routingRequestedModel(event);
}

function getRoutedModel(event) {
  return routingRoutedModel(event);
}

function getToolsInvoked(event) {
  const meta = getMetadata(event);
  return normalizeList(event?.tools_invoked || meta.tools_invoked || meta.tools || []);
}

function getDataAccessed(event) {
  const meta = getMetadata(event);
  return normalizeList(event?.data_accessed || meta.data_accessed || meta.scopes || []);
}

function formatThreat(event) {
  const meta = getMetadata(event);
  return titleCase(event.category || meta.threat_category || meta.threat_type || event.subcategory || "Unknown");
}

function formatTimestamp(timestamp) {
  if (!timestamp) return "--";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toISOString().replace("T", " ").slice(0, 19);
}

function normalizeList(value) {
  if (Array.isArray(value)) {
    return value.filter(Boolean).map((item) => String(item));
  }
  if (!value) {
    return [];
  }
  return String(value)
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniqueCount(values) {
  return new Set(values.filter(Boolean)).size;
}

function countBy(values) {
  return values.reduce((acc, value) => {
    if (!value) return acc;
    const key = String(value);
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
}

function topKey(map) {
  return Object.entries(map).sort((left, right) => right[1] - left[1])[0]?.[0] || "";
}

function average(values) {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function fmtCount(value) {
  return Number(value || 0).toLocaleString();
}

function fmtPercent(value) {
  if (!value) return "0%";
  return `${Math.round(value)}%`;
}

function joinList(values) {
  return normalizeList(values).join(", ");
}

function titleCase(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}
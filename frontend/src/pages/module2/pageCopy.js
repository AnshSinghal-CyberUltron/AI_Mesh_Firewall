/** Analyst-facing microcopy for Module 2 pages — banners and shared tooltip phrases. */

export const ANALYST_BRIEF_TITLE = "Page Objective";

export const PAGE_BRIEFS = {
  dashboard:
    "SOC command view across Chat, RAG & retrieval, MCP, and Threat Intel lanes. Scan live gateway pressure and lane totals—then open API Key & Identity Risk, Threat Intelligence Ops, Model & RAG Health, or MCP & Context Risk for deep dives.",
  ueba:
    "Identity-centric UEBA for API keys. Baseline normal usage, surface anomaly flags, and correlate key activity to vector collections and MCP tools when investigating compromise or data harvesting.",
  modelRag:
    "Review how exposed your models are and how healthy RAG searches look. Use Model Exposure for model risk, and RAG & Retrieval for search-step health and document-library risk. Hover the info icon on each KPI card for a plain-language explanation.",
  mcp:
    "See which connected tools and servers trigger blocks or redactions. Hover the info icon on each KPI card to learn what that number means.",
  threatIntel:
    "Manage your IOC library (patterns pushed to the gateway Redis cache) and measure live enforcement telemetry separately. The indicator table is configuration; KPIs and charts reflect blocks, redactions, and IOC matches from production or simulator traffic.",
  incidents:
    "Incident triage queue with enforcement-lane attribution. RAG & retrieval includes knowledge-base pipeline cases and standalone document-library lookups. That is not the same as Model & RAG Health stage KPIs — Health splits denials vs pipeline stages and shows collection risk.",
  incidentDetail:
    "Single-incident forensics: replay the enforcement timeline, inspect event metadata, and validate pipeline-stage actions before escalation or closure.",
};

/** Incidents analyst guide - shown in the incident queue info panel. */
export const INCIDENTS_GUIDE = {
  objective:
    "Triage your organization's security incidents in one place: see what needs attention, understand which enforcement lane raised each case, take action (escalate or resolve), and drill into forensics when you need full context.",
  incidentDefinition:
    "A security incident is a formal case opened when an alert rule or anomaly job decides an enforcement event is serious enough to track. It is not the same as a single block in the gateway — it is the SOC record your team works until the threat is understood and closed.",
  escalationDefinition:
    "Escalation marks a case for senior review or incident response. The status moves to Escalated so managers and IR teams can prioritize it. Use this when impact is unclear, data may be at risk, or you need help beyond first-line triage.",
  resolveDefinition:
    "Resolve closes a case when investigation is complete and the threat is contained or deemed a false positive. Resolved incidents stay in history for audit but leave the active workload.",
  tableSummary:
    "Each row is one incident case. You will see where it came from (chat, RAG, MCP, threat intel, etc.), how severe it is, and the model or API key involved. Use the case ID to open full timeline and evidence, or use Escalate / Resolve directly from the table.",
  kpiHelp:
    "The KPI cards at the top count your org queue for the selected time window (they do not shrink when you filter the table). Click a card to filter the table to that slice — for example Open or Escalated. Click again to clear. The lane chart below follows the table filters.",
  filterHelp: {
    search: "Find cases by words in the title or analyst notes.",
    status: "Show only incidents in one workflow state: Open, Investigating, Escalated, or Resolved.",
    severity: "Limit the table to Critical, High, Medium, or Low business-impact tiers.",
    source:
      "Show cases tied to a specific enforcement lane. RAG & retrieval includes knowledge-base pipeline events and standalone document-library lookups. Compare Health → Policy / Access Denials and Collection Block Rate for the same families without requiring an incident case.",
    activeQueue: "The Active Queue KPI shows open + investigating + escalated work still in progress.",
  },
};

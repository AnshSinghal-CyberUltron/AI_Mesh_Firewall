/** Analyst-facing microcopy for Module 2 pages — banners and shared tooltip phrases. */

export const ANALYST_BRIEF_TITLE = "Page Objective";

export const PAGE_BRIEFS = {
  dashboard:
    "SOC command view across Chat, RAG, Vector, and MCP enforcement lanes. Scan live pressure, risky API keys, and open incidents—then open M2.3 (Model & RAG) or M2.4 (MCP) for lane-specific drill-down.",
  ueba:
    "Identity-centric UEBA for API keys. Baseline normal usage, surface anomaly flags, and correlate key activity to vector collections and MCP tools when investigating compromise or data harvesting.",
  modelRag:
    "Assess LLM attack surface and RAG retrieval health. Tab A covers model exposure and block posture; Tab B covers pipeline-stage failures and vector collection risk.",
  mcp:
    "See which MCP tools and servers trigger blocks or redactions. Inbound scans inspect tool arguments before they reach the model; outbound scans trim sensitive data in tool responses.",
  threatIntel:
    "Manage your IOC library (patterns pushed to the gateway Redis cache) and measure live enforcement telemetry separately. The indicator table is configuration; KPIs and charts reflect blocks, redactions, and IOC matches from production or simulator traffic.",
  incidents:
    "Incident triage queue with enforcement-lane attribution. KPIs reflect your full org queue; filters narrow the table. Open any row for chain-of-custody detail, evidence, and escalation.",
  incidentDetail:
    "Single-incident forensics: replay the enforcement timeline, inspect event metadata, and validate pipeline-stage actions before escalation or closure.",
};

/** M2.6 analyst guide — shown in the incident queue info panel. */
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
    "Each row is one incident case. You will see where it came from (chat, RAG, MCP, UEBA, threat intel, etc.), how severe it is, who it is assigned to, and the model or API key involved. Use the case ID to open full timeline and evidence, or use Escalate / Resolve directly from the table.",
  kpiHelp:
    "The KPI cards at the top count your entire org queue (they do not shrink when you filter the table). Click a card to filter the table to that slice — for example Open or Escalated. Click again to clear.",
  filterHelp: {
    search: "Find cases by words in the title or analyst notes.",
    status: "Show only incidents in one workflow state: Open, Investigating, Escalated, or Resolved.",
    severity: "Limit the table to Critical, High, Medium, or Low business-impact tiers.",
    source: "Show cases tied to a specific enforcement lane — useful when you are investigating chat abuse vs MCP tool risk vs API key behavior.",
    activeQueue: "The Active Queue KPI shows open + investigating + escalated work still in progress.",
  },
};

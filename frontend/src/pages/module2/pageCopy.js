/** Analyst-facing microcopy for Module 2 pages — banners and shared tooltip phrases. */

export const ANALYST_BRIEF_TITLE = "Page Objective";

export const PAGE_BRIEFS = {
  dashboard:
    "SOC command view across Chat, RAG, Vector, and MCP enforcement lanes. Scan live pressure, risky API keys, and open incidents—then open M2.3 (Model & RAG) or M2.4 (MCP) for lane-specific drill-down.",
  ueba:
    "Identity-centric UEBA for API keys. Baseline normal usage, surface anomaly flags, and correlate key activity to vector collections and MCP tools when investigating compromise or data harvesting.",
  uebaLearning:
    "Configure org-wide graduation thresholds and review API keys still in learning mode. Keys graduate when lifetime request count or key age crosses your org thresholds.",
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

/** M2.2 UEBA modes — analyst guide on the learning & settings sub-page. */
export const UEBA_MODES_GUIDE = {
  title: "Understanding UEBA modes",
  intro:
    "Every API key moves through two scoring phases. Learning mode scores absolute risk from live traffic; active mode scores deviation from a stored behavior baseline.",
  learningMode: {
    title: "Learning mode",
    body:
      "New or reset keys start in learning mode. Risk is computed from current-period signals only — block rate, threat severity, request velocity, and policy escalations — without comparing to historical norms. Scores can still flag obvious abuse (high block rate, velocity spikes, wide model spread), but the band may be capped when sample size is small. Keys tagged as Scanner use fast-track graduation (default 10 requests or 1 day) and their risk band is capped at medium during learning so steady high block rates from security scanners do not trigger false high-risk alerts before active deviation scoring kicks in.",
  },
  activeMode: {
    title: "Active mode",
    body:
      "After graduation, the key switches to active mode. Risk is no longer judged on raw block rate alone; it measures how much today's 24-hour window diverges from the key's baseline. This reduces false positives for keys that normally run hot (e.g. security scanners) while surfacing sudden changes in volume, models used, or enforcement outcomes. Block deviation is suppressed until enough events are in the current window; model novelty is suppressed until the baseline has sufficient samples.",
  },
  behaviorBaseline: {
    title: "Behavior baseline",
    body:
      "The baseline is a profile of normal activity built from the prior 7 days of events, excluding the most recent 24 hours (the same window used for live scoring). That separation keeps today's anomaly from diluting the reference profile. It includes average block and redact rates, typical requests per hour (with variance), and commonly used models. After graduation the baseline snapshot is locked once it has enough samples; hourly refresh updates only keys without a mature locked baseline.",
  },
  deviation: {
    title: "Deviation scoring",
    body:
      "In active mode, deviation compares the current 24-hour window to the baseline. Block-rate deviation measures how far today's block share sits from the baseline average (with guards for near-zero baselines and small sample sizes). Volume z-score catches request bursts relative to typical hourly traffic. Model novelty flags models not seen in the baseline profile once the baseline is mature. These factors combine into the traditional UEBA score; optional LLM triage applies a weighted adjustment (final = traditional + 0.45 × LLM delta) before the risk band is assigned.",
  },
  graduationNote:
    "Graduation is OR-based: meeting either the request count or the age threshold is enough. Org defaults are configured in Org UEBA Settings below; per-key overrides can be set from each key's risk profile on the main UEBA page.",
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

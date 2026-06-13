/** Analyst-facing microcopy for Module 2 pages — banners and shared tooltip phrases. */

export const ANALYST_BRIEF_TITLE = "Page Objective";

export const PAGE_BRIEFS = {
  dashboard:
    "SOC command view across Chat, RAG, Vector, and MCP enforcement lanes. Scan live pressure, risky API keys, and open incidents—then pivot to lane-specific pages for deeper investigation.",
  ueba:
    "Identity-centric UEBA for API keys. Baseline normal usage, surface anomaly flags, and correlate key activity to vector collections and MCP tools when investigating compromise or data harvesting.",
  modelRag:
    "Assess LLM attack surface and RAG retrieval health. Tab A covers model exposure and block posture; Tab B covers pipeline-stage failures and vector collection risk.",
  mcp:
    "Monitor Model Context Protocol tool traffic for policy violations. Identify which tools and servers drive inbound argument blocks and outbound context minimization drops.",
  threatIntel:
    "Operate your IOC library and measure live match effectiveness. Tune indicators, confirm gateway Redis sync, and see which pipeline stages your feeds are protecting.",
  incidents:
    "Incident triage queue with enforcement-lane attribution. Filter by source, severity, and status—open any row for chain-of-custody detail and forensic evidence.",
  incidentDetail:
    "Single-incident forensics: replay the enforcement timeline, inspect event metadata, and validate pipeline-stage actions before escalation or closure.",
};

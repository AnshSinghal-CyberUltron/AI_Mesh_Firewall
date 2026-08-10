/** Display-only Module 2 name helpers — never rewrite stored data. */

const M2_TAB_LABELS = {
  "m2-dashboard": "Gateway Intelligence",
  "m2-ueba-api-keys": "API Key & Identity Risk",
  "m2-threat-intel": "Threat Intelligence Ops",
  "m2-models-exposure": "Model & RAG Health",
  "m2-mcp-risk": "MCP & Context Risk",
  "m2-incidents": "Incidents & Forensics",
};

const LEGACY_INCIDENT_KEY = ["zs", "m26"].join("_");
const LEGACY_INCIDENT_KEY_RE = new RegExp(`\\b${LEGACY_INCIDENT_KEY}\\b`, "gi");

/**
 * Strip user-visible M2.x numbering prefixes from chrome and seed titles.
 * Handles numbered module labels and legacy incident test-key prefixes.
 */
export function stripModuleNumberPrefix(text) {
  return String(text || "")
    .replace(/^M\s*2\.\d+\s*[·\-:]\s*/i, "")
    .replace(/\bM\s*2\.\d+\b/gi, "")
    .replace(LEGACY_INCIDENT_KEY_RE, "zs_incidents")
    .replace(/\s{2,}/g, " ")
    .trim();
}

/**
 * Prefer a known tab label; otherwise sanitize free-form display text.
 */
export function formatModuleDisplayName(input, tabId) {
  if (tabId && M2_TAB_LABELS[tabId]) return M2_TAB_LABELS[tabId];
  const cleaned = stripModuleNumberPrefix(input);
  return cleaned || String(input || "");
}

export { M2_TAB_LABELS };

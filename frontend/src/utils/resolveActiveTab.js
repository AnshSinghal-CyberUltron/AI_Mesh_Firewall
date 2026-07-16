const MODULE2_ROUTE_TO_TAB = {
  "/dashboard": "m2-dashboard",
  "/ueba/api-keys": "m2-ueba-api-keys",
  "/models/exposure": "m2-models-exposure",
  "/mcp/risk": "m2-mcp-risk",
  "/threat-intel": "m2-threat-intel",
  "/threats/intelligence": "m2-threat-intel",
  "/incidents": "m2-incidents",
};

const MODULE3_ROUTE_TO_TAB = {
  "/infrastructure/llmops": "m3-llmops",
  "/infrastructure/k8s-firewall": "m3-k8s-firewall",
  "/infrastructure/api-governance": "m3-api-governance",
};

const TAB_TO_ROUTE = {
  "m2-dashboard": "/dashboard",
  "m2-ueba-api-keys": "/ueba/api-keys",
  "m2-models-exposure": "/models/exposure",
  "m2-mcp-risk": "/mcp/risk",
  "m2-threat-intel": "/threat-intel",
  "m2-incidents": "/incidents",
  "m3-llmops": "/infrastructure/llmops",
  "m3-k8s-firewall": "/infrastructure/k8s-firewall",
  "m3-api-governance": "/infrastructure/api-governance",
};

export function resolveActiveTab(pathname, searchParams) {
  if (pathname.startsWith("/incidents/")) return "m2-incidents";
  if (MODULE3_ROUTE_TO_TAB[pathname]) return MODULE3_ROUTE_TO_TAB[pathname];
  if (MODULE2_ROUTE_TO_TAB[pathname]) return MODULE2_ROUTE_TO_TAB[pathname];
  return searchParams.get("tab") || "firewall";
}

export function routeForTab(tab) {
  return TAB_TO_ROUTE[tab] || null;
}

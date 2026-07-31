const MODULE2_ROUTE_TO_TAB = {
  "/dashboard": "m2-dashboard",
  "/ueba/api-keys": "m2-ueba-api-keys",
  "/threat-intel": "m2-threat-intel",
  "/threats/intelligence": "m2-threat-intel",
  "/models/exposure": "m2-models-exposure",
  "/mcp/risk": "m2-mcp-risk",
  "/incidents": "m2-incidents",
};

const TAB_TO_ROUTE = {
  "m2-dashboard": "/dashboard",
  "m2-ueba-api-keys": "/ueba/api-keys",
  "m2-threat-intel": "/threat-intel",
  "m2-models-exposure": "/models/exposure",
  "m2-mcp-risk": "/mcp/risk",
  "m2-incidents": "/incidents",
};

export function resolveActiveTab(pathname, searchParams) {
  if (pathname.startsWith("/incidents/")) return "m2-incidents";
  if (MODULE2_ROUTE_TO_TAB[pathname]) return MODULE2_ROUTE_TO_TAB[pathname];
  return searchParams.get("tab") || "firewall";
}

export function routeForTab(tab) {
  return TAB_TO_ROUTE[tab] || null;
}

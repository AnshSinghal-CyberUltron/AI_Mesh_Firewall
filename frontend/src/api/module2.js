/** Module 2 API helpers — UEBA + Threat Intelligence scope. */

const GET_CACHE = new Map();
const CACHE_TTL_MS = 30_000;

export function clearModule2Cache() {
  GET_CACHE.clear();
}

function buildCacheKey(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  return `/api/module2${path}${qs ? `?${qs}` : ""}`;
}

export function createModule2Api(fetchWithAuth) {
  const get = async (path, params = {}, { useCache = true } = {}) => {
    const url = buildCacheKey(path, params);
    if (useCache) {
      const cached = GET_CACHE.get(url);
      if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
        return cached.data;
      }
    }
    const res = await fetchWithAuth(url);
    if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
    const data = await res.json();
    if (useCache) {
      GET_CACHE.set(url, { at: Date.now(), data });
    }
    return data;
  };

  const mutate = async (path, method, body) => {
    const res = await fetchWithAuth(`/api/module2${path}`, {
      method,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `API error ${res.status}`);
    }
    GET_CACHE.clear();
    if (res.status === 204) return null;
    return res.json();
  };

  return {
    getDashboard: (period = "24h", opts = {}) => get("/dashboard/", { period }, opts),
    getUebaSummary: (period = "24h", opts = {}) => get("/ueba/api-keys/summary/", { period }, opts),
    getUebaTimeline: (period = "24h", opts = {}) => get("/ueba/api-keys/timeline/", { period }, opts),
    getUebaRegistry: (period = "24h", opts = {}) => get("/ueba/api-keys/registry/", { period }, opts),
    getUebaBehavior: (keyId, period = "7d") => get(`/ueba/api-keys/${keyId}/behavior/`, { period }),
    getModelExposure: (period = "30d") => get("/models/exposure/", { period }),
    getRagHealth: (period = "24h") => get("/rag/health/", { period }),
    getMcpRisk: (period = "24h") => get("/mcp/risk/", { period }),
    getThreatTelemetry: (period = "7d") => get("/threat-intel/telemetry/", { period }),

    listThreatIntel: () => get("/threat-intel/"),
    createThreatIntel: (data) => mutate("/threat-intel/", "POST", data),
    updateThreatIntel: (id, data) => mutate(`/threat-intel/${id}/`, "PATCH", data),
    deleteThreatIntel: (id) => mutate(`/threat-intel/${id}/`, "DELETE"),
    syncThreatIntel: () => mutate("/threat-intel/sync/", "POST"),

    listIncidents: (filters = {}) => {
      const params = {};
      if (filters.status) params.status = filters.status;
      if (filters.severity) params.severity = filters.severity;
      if (filters.source) params.source = filters.source;
      if (filters.search) params.search = filters.search;
      if (filters.page) params.page = String(filters.page);
      if (filters.page_size) params.page_size = String(filters.page_size);
      return get("/incidents/", params);
    },
    getIncident: (id) => get(`/incidents/${id}/`),
  };
}

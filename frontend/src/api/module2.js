/** Module 2 API helpers — UEBA + Threat Intelligence scope. */

const GET_CACHE = new Map();
const CACHE_TTL_MS = 30_000;
let CACHE_SCOPE = "anon";

export const INCIDENT_QUEUE_MUTATED_EVENT = "ai-mesh:incident-queue-mutated";

export function clearModule2Cache() {
  GET_CACHE.clear();
}

export function setModule2CacheScope(scopeKey) {
  const nextScope = String(scopeKey || "anon");
  if (nextScope !== CACHE_SCOPE) {
    clearModule2Cache();
    CACHE_SCOPE = nextScope;
  }
}

function notifyIncidentQueueMutated() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(INCIDENT_QUEUE_MUTATED_EVENT));
  }
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
    const res = await fetchWithAuth(
      useCache ? url : `${url}${url.includes("?") ? "&" : "?"}_=${Date.now()}`,
    );
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
    getUebaBehavior: (keyId, period = "24h", opts = {}) => get(`/ueba/api-keys/${keyId}/behavior/`, { period }, opts),
    getModelExposure: (period = "24h", opts = {}) => get("/models/exposure/", { period }, opts),
    getRagHealth: (period = "24h", opts = {}) => get("/rag/health/", { period }, opts),
    getMcpRisk: (period = "24h", opts = {}) => get("/mcp/risk/", { period }, opts),
    getThreatTelemetry: (period = "7d", opts = {}) => get("/threat-intel/telemetry/", { period }, opts),

    listThreatIntel: () => get("/threat-intel/"),
    createThreatIntel: (data) => mutate("/threat-intel/", "POST", data),
    updateThreatIntel: (id, data) => mutate(`/threat-intel/${id}/`, "PATCH", data),
    deleteThreatIntel: (id) => mutate(`/threat-intel/${id}/`, "DELETE"),
    syncThreatIntel: () => mutate("/threat-intel/sync/", "POST"),

    listIncidents: (filters = {}, opts = {}) => {
      const params = {};
      if (filters.status) params.status = filters.status;
      if (filters.queue) params.queue = filters.queue;
      if (filters.severity) params.severity = filters.severity;
      if (filters.source) params.source = filters.source;
      if (filters.search) params.search = filters.search;
      if (filters.page) params.page = String(filters.page);
      if (filters.page_size) params.page_size = String(filters.page_size);
      return get("/incidents/", params, opts);
    },
    getIncident: (id, opts = {}) => get(`/incidents/${id}/`, {}, opts),
    escalateIncident: async (id) => {
      const res = await fetchWithAuth(`/api/security/incidents/${id}/escalate-incident/`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Escalate failed (${res.status})`);
      }
      clearModule2Cache();
      notifyIncidentQueueMutated();
      return res.json();
    },
    resolveIncident: async (id) => {
      const res = await fetchWithAuth(`/api/security/incidents/${id}/resolve-incident/`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Resolve failed (${res.status})`);
      }
      clearModule2Cache();
      notifyIncidentQueueMutated();
      return res.json();
    },
  };
}

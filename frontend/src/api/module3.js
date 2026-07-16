/** Module 3 API helpers — LLMOps pipeline + K8s firewall. */

const GET_CACHE = new Map();
const CACHE_TTL_MS = 30_000;
let CACHE_SCOPE = "anon";

export function clearModule3Cache() {
  GET_CACHE.clear();
}

export function setModule3CacheScope(scopeKey) {
  const nextScope = String(scopeKey || "anon");
  if (nextScope !== CACHE_SCOPE) {
    clearModule3Cache();
    CACHE_SCOPE = nextScope;
  }
}

function buildCacheKey(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  return `/api/module3${path}${qs ? `?${qs}` : ""}`;
}

export function createModule3Api(fetchWithAuth) {
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
    const res = await fetchWithAuth(`/api/module3${path}`, {
      method,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || err.reason || `API error ${res.status}`);
    }
    GET_CACHE.clear();
    if (res.status === 204) return null;
    return res.json();
  };

  return {
    getLlmopsSummary: (period = "24h", opts = {}) => get("/llmops/summary/", { period }, opts),
    getArtifacts: (page = 1, pageSize = 25, opts = {}) =>
      get("/llmops/artifacts/", { page: String(page), page_size: String(pageSize) }, opts),
    getAdmissionLog: (period = "24h", page = 1, pageSize = 25, opts = {}) =>
      get(
        "/llmops/admission-log/",
        { period, page: String(page), page_size: String(pageSize) },
        opts,
      ),
    getPipelineRuns: (period = "24h", page = 1, pageSize = 25, opts = {}) =>
      get(
        "/llmops/pipeline-runs/",
        { period, page: String(page), page_size: String(pageSize) },
        opts,
      ),
    registerArtifact: (data) => mutate("/llmops/artifacts/", "POST", data),
    verifyArtifact: (data) => mutate("/llmops/verify/", "POST", data),

    getK8sFirewallSummary: (period = "24h", opts = {}) =>
      get("/k8s-firewall/summary/", { period }, opts),
    getTopology: (opts = {}) => get("/k8s-firewall/topology/", {}, opts),
    getNetworkEvents: (period = "24h", filters = {}, opts = {}) => {
      const params = { period, page: String(filters.page || 1), page_size: String(filters.page_size || 25) };
      if (filters.layer) params.layer = filters.layer;
      if (filters.action) params.action = filters.action;
      return get("/k8s-firewall/network-events/", params, opts);
    },
    getEmbeddingQueue: (period = "24h", filters = {}, opts = {}) => {
      const params = { period, page: String(filters.page || 1), page_size: String(filters.page_size || 25) };
      if (filters.status) params.status = filters.status;
      return get("/k8s-firewall/embedding-queue/", params, opts);
    },
    getApiGovernanceSummary: (opts = {}) => get("/api-governance/summary/", {}, opts),
    getApiGovernancePolicies: (page = 1, pageSize = 25, opts = {}) =>
      get("/api-governance/policies/", { page: String(page), page_size: String(pageSize) }, opts),
    getApiGovernanceEvents: (filters = {}, opts = {}) => {
      const params = { page: String(filters.page || 1), page_size: String(filters.page_size || 25) };
      if (filters.action) params.action = filters.action;
      return get("/api-governance/events/", params, opts);
    },
    createApiGovernancePolicy: (data) => mutate("/api-governance/policies/", "POST", data),
    simulatorIngest: (data) => mutate("/simulator/ingest/", "POST", data),
  };
}

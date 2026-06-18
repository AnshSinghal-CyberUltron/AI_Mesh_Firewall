import test from "node:test";
import assert from "node:assert/strict";
import { createModule2Api, clearModule2Cache, setModule2CacheScope } from "./module2.js";

test("listIncidents builds query params for filters and pagination", async () => {
  const calls = [];
  const fetchWithAuth = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ count: 0, results: [] }) };
  };
  const api = createModule2Api(fetchWithAuth);
  await api.listIncidents({
    status: "open",
    severity: "high",
    source: "ueba",
    search: "injection",
    page: 2,
    page_size: 25,
  });
  assert.equal(calls.length, 1);
  const url = calls[0];
  assert.ok(url.includes("/api/module2/incidents/"));
  assert.ok(url.includes("status=open"));
  assert.ok(url.includes("severity=high"));
  assert.ok(url.includes("source=ueba"));
  assert.ok(url.includes("search=injection"));
  assert.ok(url.includes("page=2"));
  assert.ok(url.includes("page_size=25"));
});

test("listIncidents accepts cache-bust opts", async () => {
  const calls = [];
  const fetchWithAuth = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ count: 0, results: [], summary: {} }) };
  };
  const api = createModule2Api(fetchWithAuth);
  await api.listIncidents({}, { useCache: false });
  assert.ok(calls[0].includes("_="));
});

test("escalateIncident posts to security API and clears cache", async () => {
  const calls = [];
  const fetchWithAuth = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET" });
    if (opts.method === "POST") {
      return { ok: true, json: async () => ({ id: 1, status: "escalated" }) };
    }
    return { ok: true, json: async () => ({ incident: { id: 1 }, timeline: [] }) };
  };
  const api = createModule2Api(fetchWithAuth);
  await api.getIncident(1);
  await api.escalateIncident(1);
  await api.getIncident(1);
  assert.equal(calls.filter((c) => c.method === "GET").length, 2);
  assert.ok(calls.some((c) => c.url.includes("/api/security/incidents/1/escalate-incident/")));
});

test("getThreatTelemetry requests telemetry endpoint with period", async () => {
  const calls = [];
  const fetchWithAuth = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ summary: {}, timeline: [], top_attack_vectors: [] }) };
  };
  const api = createModule2Api(fetchWithAuth);
  await api.getThreatTelemetry("24h");
  assert.ok(calls[0].includes("/api/module2/threat-intel/telemetry/"));
  assert.ok(calls[0].includes("period=24h"));
});

test("getRagHealth requests rag health endpoint with period", async () => {
  const calls = [];
  const fetchWithAuth = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ rag_pipeline_kpis: {}, vector_exposure: {} }) };
  };
  const api = createModule2Api(fetchWithAuth);
  await api.getRagHealth("30d");
  assert.equal(calls.length, 1);
  assert.ok(calls[0].includes("/api/module2/rag/health/"));
  assert.ok(calls[0].includes("period=30d"));
});

test("getMcpRisk requests mcp risk endpoint with period and uses GET cache", async () => {
  const calls = [];
  const fetchWithAuth = async (url) => {
    calls.push(url);
    return {
      ok: true,
      json: async () => ({ summary: {}, tool_ledger: [], direction_split: {}, top_servers: [] }),
    };
  };
  const api = createModule2Api(fetchWithAuth);
  const first = await api.getMcpRisk("7d");
  const second = await api.getMcpRisk("7d");
  assert.equal(calls.length, 1, "second call should be served from cache");
  assert.ok(calls[0].includes("/api/module2/mcp/risk/"));
  assert.ok(calls[0].includes("period=7d"));
  assert.deepEqual(second, first);
});

test("cache is invalidated when auth scope changes", async () => {
  clearModule2Cache();
  setModule2CacheScope("org-a:user-a");

  const calls = [];
  const fetchWithAuth = async (url) => {
    calls.push(url);
    return {
      ok: true,
      json: async () => ({ summary: {}, tool_ledger: [], direction_split: {}, top_servers: [] }),
    };
  };

  const api = createModule2Api(fetchWithAuth);
  await api.getMcpRisk("24h");
  await api.getMcpRisk("24h");
  assert.equal(calls.length, 1, "same scope should use cache");

  setModule2CacheScope("org-b:user-b");
  await api.getMcpRisk("24h");
  assert.equal(calls.length, 2, "scope switch must clear cache");
});

test("getModelExposure throws on non-ok response", async () => {
  const fetchWithAuth = async () => ({ ok: false, status: 500 });
  const api = createModule2Api(fetchWithAuth);
  await assert.rejects(() => api.getModelExposure(), /API error 500/);
});

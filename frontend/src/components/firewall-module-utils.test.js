import assert from "node:assert/strict";
import test from "node:test";

import { buildModulePageData, filterEventsForModule } from "./firewall-module-utils.js";

/** Shared fixture catalog — keep aligned with policy/tests/test_module_kpis_trends_parity.py */
const FIXTURE_CATALOG = [
  {
    id: "rag-pipeline",
    action: "block",
    metadata: { event_type: "rag_pipeline", source: "rag" },
    modules: { "1.1": true, "1.2": true, "1.3": true },
  },
  {
    id: "vector-query",
    action: "block",
    metadata: { event_type: "vector_query", source: "vector" },
    modules: { "1.1": true, "1.3": true },
  },
  {
    id: "embedding",
    action: "block",
    metadata: { event_type: "embedding_request", source: "vector" },
    modules: { "1.1": true, "1.3": true },
  },
  {
    id: "mcp-redact",
    action: "redact",
    metadata: { source: "mcp_scan", event_type: "tool_call" },
    modules: { "1.1": true, "1.4": true },
  },
  {
    id: "kill-switch",
    action: "block",
    metadata: {
      event_type: "kill_switch",
      source: "policy",
      module_id: "1.6",
      security_risk_score: 85,
    },
    modules: { "1.1": true, "1.6": true },
  },
  {
    id: "output-guard",
    action: "block",
    metadata: { event_type: "output_guard", source: "output" },
    modules: { "1.1": true, "1.7": true },
  },
];

function feedFromCatalog() {
  return FIXTURE_CATALOG.map((row) => ({
    id: row.id,
    action: row.action,
    metadata: row.metadata,
    timestamp: new Date().toISOString(),
  }));
}

function countForModule(moduleId, feed) {
  return filterEventsForModule(moduleId, feed).length;
}

test("filterEventsForModule counts match backend fixture expectations", () => {
  const feed = feedFromCatalog();
  for (const row of FIXTURE_CATALOG) {
    for (const [moduleId, expected] of Object.entries(row.modules)) {
      const included = countForModule(moduleId, feed.filter((ev) => ev.id === row.id));
      assert.equal(included > 0, expected, `${row.id} module ${moduleId}`);
    }
  }
});

test("rag_pipeline appears in both 1.2 and 1.3 lanes", () => {
  const feed = feedFromCatalog();
  const ragOnly = feed.filter((ev) => ev.id === "rag-pipeline");
  assert.ok(countForModule("1.2", ragOnly) > 0);
  assert.ok(countForModule("1.3", ragOnly) > 0);
});

test("vector_query appears in 1.3 but not 1.2-only threat lane", () => {
  const feed = feedFromCatalog();
  const vectorOnly = feed.filter((ev) => ev.id === "vector-query");
  assert.ok(countForModule("1.3", vectorOnly) > 0);
  assert.equal(countForModule("1.2", vectorOnly), 0);
});

test("aggregate 1.3 count includes RAG event types", () => {
  const feed = feedFromCatalog();
  const total = countForModule("1.3", feed);
  assert.ok(total >= 3, `expected >= 3 events in 1.3, got ${total}`);
});

test("module 1.1 summary cards use soc-kpis totals not capped threat-feed length", () => {
  const feed = Array.from({ length: 500 }, (_, index) => ({
    id: `ev-${index}`,
    action: index % 2 === 0 ? "allow" : "block",
    metadata: { organization_id: index % 2 === 0 ? "org-a" : "org-b" },
    timestamp: new Date().toISOString(),
  }));
  const socKpis = {
    period: "24h",
    total_threats: 1516,
    blocked: 408,
    redacted: 45,
    block_rate: 26.9,
  };

  const page = buildModulePageData("1.1", feed, { socKpis });
  const byLabel = Object.fromEntries(page.summaryCards.map((card) => [card.label, card.value]));

  assert.equal(byLabel["Requests inspected"], "1,516");
  assert.equal(byLabel["Allowed through gateway"], "1,063");
  assert.equal(byLabel["Rate-limited or blocked"], "408");
  assert.equal(byLabel["Identities observed"], "2");
});

test("module 1.1 prefers request-scoped soc-kpis (distinct count, includes blocked/failed)", () => {
  const feed = Array.from({ length: 4 }, (_, index) => ({
    id: `ev-${index}`,
    action: "allow",
    metadata: { organization_id: index % 2 === 0 ? "org-a" : "org-b" },
    timestamp: new Date().toISOString(),
  }));
  // Raw rows (total_threats=40) >> distinct requests (22) because each request
  // emits several enforcement rows. The 1.1 cards must use the request-scoped
  // partition, NOT total_threats and NOT the legacy event_type='request' count.
  const socKpis = {
    period: "24h",
    total_threats: 40,
    requests_inspected: 22,
    requests_allowed: 7,
    requests_blocked: 8,
    requests_redacted: 7,
    blocked: 12, // row-based (higher than distinct) — must NOT leak into 1.1 cards
    redacted: 9,
  };

  const page = buildModulePageData("1.1", feed, { socKpis });
  const byLabel = Object.fromEntries(page.summaryCards.map((card) => [card.label, card.value]));

  assert.equal(byLabel["Requests inspected"], "22");
  assert.equal(byLabel["Allowed through gateway"], "7");
  assert.equal(byLabel["Rate-limited or blocked"], "8");
});

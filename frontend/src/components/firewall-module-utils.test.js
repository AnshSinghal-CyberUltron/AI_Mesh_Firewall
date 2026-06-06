import assert from "node:assert/strict";
import test from "node:test";

import { filterEventsForModule } from "./firewall-module-utils.js";

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
